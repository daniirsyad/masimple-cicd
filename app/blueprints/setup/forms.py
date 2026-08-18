from flask_wtf import FlaskForm
from wtforms import SubmitField


class SetupForm(FlaskForm):
    """No real fields — this only exists to carry a CSRF token on the
    "Run Setup" button, same as every other POST in this app.
    """

    submit = SubmitField("Run Setup")
