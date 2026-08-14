from flask import flash, redirect, render_template, url_for
from flask_login import current_user, login_required
from werkzeug.security import check_password_hash, generate_password_hash

from app.blueprints.account import account_bp
from app.blueprints.account.forms import AccountForm
from app.extensions import db
from app.utils.logger import log_activity


@account_bp.route("/", methods=["GET", "POST"])
@login_required
def index():
    form = AccountForm(obj=current_user)

    if form.validate_on_submit():
        password_changed = False
        if form.new_password.data:
            if not form.current_password.data or not check_password_hash(
                current_user.password_hash, form.current_password.data
            ):
                flash("Current password is incorrect.", "error")
                return render_template("account/index.html", form=form)
            current_user.password_hash = generate_password_hash(form.new_password.data)
            password_changed = True

        current_user.full_name = form.full_name.data or None
        current_user.telegram_chat_id = form.telegram_chat_id.data or None
        db.session.commit()

        log_activity(
            action="UPDATE_OWN_ACCOUNT",
            target_type="user",
            target_id=str(current_user.id),
            description=(
                f"User '{current_user.username}' updated their own account"
                f"{' (password changed)' if password_changed else ''}"
            ),
        )

        flash("Your account has been updated.", "success")
        return redirect(url_for("account.index"))

    return render_template("account/index.html", form=form)
