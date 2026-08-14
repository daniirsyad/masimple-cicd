import hashlib
import secrets
from datetime import datetime, timedelta

from flask import flash, redirect, render_template, request, session, url_for
from flask_login import current_user, login_required, login_user, logout_user
from werkzeug.security import check_password_hash, generate_password_hash

from app.blueprints.auth import auth_bp
from app.blueprints.auth.forms import ForgotPasswordForm, LoginForm, ResetPasswordForm
from app.extensions import db
from app.models import PasswordResetToken, User
from app.services.telegram.helpers import notify_security_contact, notify_user
from app.utils.logger import log_activity
from app.utils.system_config import get_system_config

RESET_TOKEN_TTL_MINUTES = 15


@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("main.index"))

    form = LoginForm()
    if form.validate_on_submit():
        user = User.query.filter_by(username=form.username.data).first()
        max_attempts = get_system_config().max_login_attempts

        # A locked account is rejected before the password is even checked
        # — otherwise typing the right password would bypass the lock,
        # defeating the point of requiring a user.unlock permission holder
        # to clear it. Deliberately shows the same "locked" message
        # regardless of whether the password just typed was actually
        # correct, so this can't be used to probe whether a guessed
        # password was right.
        if user is not None and user.is_active and user.failed_login_attempts >= max_attempts:
            log_activity(
                action="LOGIN_BLOCKED",
                target_type="user",
                target_id=str(user.id),
                description=f"Blocked login attempt for locked account '{user.username}'",
            )
            flash("This account is locked due to too many failed login attempts. Contact an administrator to unlock it.", "error")
            return render_template("auth/login.html", form=form)

        valid = user is not None and user.is_active and check_password_hash(
            user.password_hash, form.password.data
        )

        if not valid:
            if user is not None and user.is_active:
                user.failed_login_attempts += 1
                just_locked = user.failed_login_attempts >= max_attempts
                if just_locked:
                    user.locked_at = datetime.utcnow()
                db.session.commit()

                # Goes to the designated security contact (SystemConfig.
                # security_notification_user_id), not to the affected user
                # themselves — see notify_security_contact(). Deliberately
                # the same generic flash message either way (see the
                # locked-account branch above); the account owner never
                # learns about this from the screen, only the security
                # contact does, via Telegram.
                if just_locked:
                    notify_security_contact(
                        f"🔒 Account '{user.username}' has been locked after too many failed "
                        "login attempts. A user.unlock permission holder must unlock it.",
                    )
                else:
                    notify_security_contact(
                        f"⚠️ A failed login attempt (wrong password) was just made on account '{user.username}'.",
                    )

            log_activity(
                action="LOGIN_FAILED",
                target_type="user",
                target_id=str(user.id) if user else None,
                description=f"Failed login attempt for username '{form.username.data}'",
            )
            flash("Invalid username or password.", "error")
            return render_template("auth/login.html", form=form)

        user.failed_login_attempts = 0
        user.locked_at = None
        login_user(user)
        session.permanent = True
        user.last_login_at = datetime.utcnow()
        db.session.commit()

        log_activity(
            action="LOGIN",
            target_type="user",
            target_id=str(user.id),
            description=f"User '{user.username}' logged in",
        )
        notify_security_contact(
            f"✅ User '{user.username}' just signed in to MASIMPLE CICD (IP: {request.remote_addr or 'unknown'})."
        )

        next_page = request.args.get("next")
        return redirect(next_page or url_for("main.index"))

    return render_template("auth/login.html", form=form)


@auth_bp.route("/logout")
@login_required
def logout():
    log_activity(
        action="LOGOUT",
        target_type="user",
        target_id=str(current_user.id),
        description=f"User '{current_user.username}' logged out",
    )
    logout_user()
    flash("You have been logged out.", "info")
    return redirect(url_for("auth.login"))


def _hash_token(raw_token):
    return hashlib.sha256(raw_token.encode()).hexdigest()


@auth_bp.route("/forgot-password", methods=["GET", "POST"])
def forgot_password():
    if current_user.is_authenticated:
        return redirect(url_for("main.index"))

    form = ForgotPasswordForm()
    if form.validate_on_submit():
        user = User.query.filter_by(username=form.username.data).first()

        # Only ever proceeds for a real, active user with a Telegram chat ID
        # configured — but the flash message below is identical either way
        # (see the redirect at the end of this branch), so this app never
        # reveals which usernames exist or whether Telegram is set up for
        # them.
        if user is not None and user.is_active and user.telegram_chat_id:
            raw_token = secrets.token_urlsafe(32)
            db.session.add(
                PasswordResetToken(
                    user_id=user.id,
                    token_hash=_hash_token(raw_token),
                    expires_at=datetime.utcnow() + timedelta(minutes=RESET_TOKEN_TTL_MINUTES),
                )
            )
            db.session.commit()

            reset_url = url_for("auth.reset_password", token=raw_token, _external=True)
            sent = notify_user(
                user,
                "🔑 A password reset was requested for your MASIMPLE CICD account. "
                f"If this was you, reset it here (expires in {RESET_TOKEN_TTL_MINUTES} minutes): {reset_url}\n"
                "If this wasn't you, ignore this message — your password won't change unless that link is used.",
            )
            log_activity(
                action="PASSWORD_RESET_REQUESTED",
                target_type="user",
                target_id=str(user.id),
                description=f"Password reset requested for user '{user.username}' (Telegram sent: {sent})",
            )

        flash(
            "If that account exists and has Telegram configured, a password reset link has been sent.",
            "info",
        )
        return redirect(url_for("auth.login"))

    return render_template("auth/forgot_password.html", form=form)


@auth_bp.route("/reset-password/<token>", methods=["GET", "POST"])
def reset_password(token):
    if current_user.is_authenticated:
        return redirect(url_for("main.index"))

    reset_token = PasswordResetToken.query.filter_by(token_hash=_hash_token(token)).first()
    if reset_token is None or not reset_token.is_valid():
        flash("This password reset link is invalid or has expired. Request a new one.", "error")
        return redirect(url_for("auth.forgot_password"))

    form = ResetPasswordForm()
    if form.validate_on_submit():
        user = reset_token.user
        user.password_hash = generate_password_hash(form.password.data)
        # A successful reset is also a legitimate reason to clear a lockout
        # — the user just proved account ownership via Telegram, same trust
        # level as a user.unlock permission holder resetting it by hand.
        user.failed_login_attempts = 0
        user.locked_at = None
        reset_token.used_at = datetime.utcnow()
        db.session.commit()

        log_activity(
            action="PASSWORD_RESET",
            target_type="user",
            target_id=str(user.id),
            description=f"Password reset via Telegram link for user '{user.username}'",
        )
        notify_user(user, "✅ Your MASIMPLE CICD password was just reset. If this wasn't you, contact an administrator immediately.")

        flash("Your password has been reset. You can now log in.", "success")
        return redirect(url_for("auth.login"))

    return render_template("auth/reset_password.html", form=form, token=token)
