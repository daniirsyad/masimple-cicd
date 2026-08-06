from datetime import datetime

from flask import flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required, login_user, logout_user
from werkzeug.security import check_password_hash

from app.blueprints.auth import auth_bp
from app.blueprints.auth.forms import LoginForm
from app.extensions import db
from app.models import User
from app.utils.logger import log_activity


@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("main.index"))

    form = LoginForm()
    if form.validate_on_submit():
        user = User.query.filter_by(username=form.username.data).first()
        valid = user is not None and user.is_active and check_password_hash(
            user.password_hash, form.password.data
        )

        if not valid:
            log_activity(
                action="LOGIN_FAILED",
                target_type="user",
                target_id=str(user.id) if user else None,
                description=f"Failed login attempt for username '{form.username.data}'",
            )
            flash("Invalid username or password.", "error")
            return render_template("auth/login.html", form=form)

        login_user(user)
        user.last_login_at = datetime.utcnow()
        db.session.commit()

        log_activity(
            action="LOGIN",
            target_type="user",
            target_id=str(user.id),
            description=f"User '{user.username}' logged in",
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
