# Project Plan: Hamilton — Build & Deployment Application (Flask Foundation)

This document is a **plan/spec** you can use as context for GitHub Copilot (Copilot Chat / Copilot Workspace) so the AI builds the application foundation with a consistent structure.

---

## 1. Tech Stack

| Layer      | Technology                                                 |
| ---------- | ---------------------------------------------------------- |
| Backend    | Flask (Application Factory Pattern)                        |
| ORM        | SQLAlchemy + Flask-Migrate (Alembic)                       |
| Database   | PostgreSQL                                                 |
| Auth       | Flask-Login + Flask-WTF (CSRF) + Werkzeug password hashing |
| Frontend   | Jinja2 + TailwindCSS + daisyUI                             |
| Deployment | Docker + Docker Compose                                    |
| Env config | python-dotenv                                              |

---

## 2. Folder Structure (recommended)

```
hamilton/
├── app/
│   ├── __init__.py            # App factory
│   ├── extensions.py          # db, login_manager, migrate, csrf
│   ├── models/
│   │   ├── user.py
│   │   ├── role.py
│   │   ├── permission.py
│   │   ├── menu.py            # sidebar/navbar menu items
│   │   └── activity_log.py
│   ├── blueprints/
│   │   ├── auth/
│   │   │   ├── routes.py
│   │   │   └── forms.py
│   │   ├── main/               # Home page
│   │   │   └── routes.py
│   │   ├── users/               # User management + edit-user page
│   │   │   ├── routes.py
│   │   │   └── forms.py
│   │   ├── roles/               # Role management
│   │   │   ├── routes.py
│   │   │   └── forms.py
│   │   └── logs/                # Activity logs viewer
│   │       └── routes.py
│   ├── utils/
│   │   ├── decorators.py       # @permission_required, @role_required
│   │   ├── menu_builder.py     # builds sidebar/navbar tree from Menu table
│   │   └── logger.py           # helper to record activity log
│   ├── templates/
│   │   ├── base.html            # layout: navbar + sidebar wrapper
│   │   ├── partials/
│   │   │   ├── navbar.html
│   │   │   └── sidebar.html
│   │   ├── auth/
│   │   ├── main/
│   │   ├── users/
│   │   │   ├── list.html
│   │   │   └── edit.html        # dedicated edit-user page per user
│   │   ├── roles/
│   │   └── logs/
│   └── static/
│       └── src/
│           └── input.css       # TailwindCSS source
├── migrations/
├── seeds/
│   ├── seed_admin.py           # creates 1 initial super admin (no signup flow)
│   └── seed_menu.py            # seeds default sidebar/navbar menu tree
├── config.py
├── requirements.txt
├── package.json                # for tailwindcss + daisyUI build
├── tailwind.config.js
├── Dockerfile
├── docker-compose.yml
├── .env.example
├── .dockerignore
├── .gitignore
└── run.py
```

---

## 3. Database Schema (PostgreSQL)

Since roles are **dynamic** (admin can create/edit roles), use an **RBAC structure with a separate permission table** instead of a hardcoded enum.

### `users`

| Column        | Type                     | Notes                    |
| ------------- | ------------------------ | ------------------------ |
| id            | UUID / Integer PK        |                          |
| username      | String, unique, not null | used for login           |
| password_hash | String, not null         |                          |
| full_name     | String                   |                          |
| is_active     | Boolean, default True    | for suspending a user    |
| role_id       | FK → roles.id            |                          |
| created_by    | FK → users.id (nullable) | who created this account |
| created_at    | DateTime                 |                          |
| last_login_at | DateTime, nullable       |                          |

### `roles`

| Column      | Type                   | Notes                                         |
| ----------- | ---------------------- | --------------------------------------------- |
| id          | PK                     |                                               |
| name        | String, unique         | e.g. "Admin", "Developer", "Viewer"           |
| description | Text, nullable         |                                               |
| is_system   | Boolean, default False | built-in role (Super Admin) cannot be deleted |
| created_at  | DateTime               |                                               |

### `permissions`

| Column      | Type           | Notes                                        |
| ----------- | -------------- | -------------------------------------------- |
| id          | PK             |                                              |
| code        | String, unique | e.g. `user.create`, `role.edit`, `logs.view` |
| description | String         |                                              |

### `role_permissions` (many-to-many)

| Column        | Type                |
| ------------- | ------------------- |
| role_id       | FK → roles.id       |
| permission_id | FK → permissions.id |

### `menus` (sidebar & navbar items)

| Column          | Type                    | Notes                                                                                                                           |
| --------------- | ----------------------- | ------------------------------------------------------------------------------------------------------------------------------- |
| id              | PK                      |                                                                                                                                 |
| label           | String                  | display name, e.g. "Management"                                                                                                 |
| icon            | String, nullable        | icon class/name                                                                                                                 |
| url             | String, nullable        | route path; nullable if it's a parent-only group                                                                                |
| parent_id       | FK → menus.id, nullable | **max depth: 1 level** — a menu whose `parent_id` is not null cannot have children of its own (enforce in app logic / DB check) |
| permission_code | String, nullable        | FK-like reference to `permissions.code`; menu item hidden if user lacks this permission                                         |
| order           | Integer, default 0      | sort order within its level                                                                                                     |
| show_in_navbar  | Boolean, default False  | also show as a quick-access item in the navbar                                                                                  |
| show_in_sidebar | Boolean, default True   |                                                                                                                                 |

### `activity_logs`

| Column      | Type                    | Notes                                      |
| ----------- | ----------------------- | ------------------------------------------ |
| id          | PK                      |                                            |
| user_id     | FK → users.id, nullable | nullable if performed by the system        |
| action      | String                  | e.g. `LOGIN`, `CREATE_USER`, `UPDATE_ROLE` |
| target_type | String, nullable        | e.g. `user`, `role`                        |
| target_id   | String, nullable        |                                            |
| description | Text                    | human-readable detail                      |
| ip_address  | String, nullable        |                                            |
| created_at  | DateTime                |                                            |

> Note: since "other features will be built independently", the tables above are intentionally generic/extensible (permission-based, menu-driven) so future modules can add new `permission.code` and `menu` entries without touching the core structure.

---

## 4. Authentication System (No Signup)

- No public register page/route.
- Accounts can only be created by an Admin via the **User Management** page.
- On first project setup, run `seeds/seed_admin.py` to create 1 default **Super Admin** account (role `is_system=True`, cannot be deleted).
- Login uses **username + password**.
- Use `Flask-Login` for session management, `werkzeug.security` (`generate_password_hash` / `check_password_hash`) for hashing.
- Add simple rate-limiting / lockout after repeated failed logins (optional, e.g. `Flask-Limiter`).
- Every successful/failed login is recorded in `activity_logs`.

---

## 5. Role & Permission Management

- Role CRUD (create, edit, delete — except `is_system` roles).
- When creating/editing a role, admin selects which permissions to assign (checkbox list from the `permissions` table).
- Build a `@permission_required("user.create")` decorator to protect routes based on permission, not role name — keeping things flexible since roles are dynamic.
- Seed an initial set of base permissions, e.g.:
  - `user.view`, `user.create`, `user.edit`, `user.delete`
  - `role.view`, `role.create`, `role.edit`, `role.delete`
  - `logs.view`
  - `menu.view`, `menu.edit` (if menu management is also admin-editable)

---

## 6. User Management

- User list (with search & filter by role/status).
- Create user (admin sets username, initial password, selects role).
- **Dedicated Edit User page** (`/users/<id>/edit`) per user — update full name, role, active/inactive status, reset password. Linked from the user list row (e.g. an "Edit" button/icon).
- Avoid hard delete — use soft-delete (`is_active=False`) so activity log history stays valid.

---

## 7. Home Page

- Lightweight dashboard after login: greeting, summary counts (active users, roles), and a few recent activity log entries.
- Also serves as the "foundation" for the other features you'll build yourself later (build & deployment for Hamilton).

---

## 8. Activity Logs

- All key actions (login, logout, create/edit/delete user, create/edit/delete role, menu changes) are recorded automatically via a `log_activity(user, action, target_type, target_id, description)` helper.
- **Logs** page (behind `logs.view` permission) shows a table of logs filterable by user, action, and date.

---

## 9. Layout: Sidebar + Navbar (TailwindCSS + daisyUI)

**Layout requirements:**

- Every authenticated page uses a shared layout (`base.html`) with a **fixed Navbar** on top and a **Sidebar** on the left (daisyUI `drawer` component).
- **Sidebar** contains the main navigation, including a **"Management" menu** (grouping Users, Roles, Logs, etc.).
- **Menus can be nested, max 1 level deep** (parent → child only, no grandchildren). Example:
  - `Management` (parent, group only, no direct link)
    - `Users` (child → `/users`)
    - `Roles` (child → `/roles`)
    - `Activity Logs` (child → `/logs`)
  - `Home` (single-level item, no children)
- Menu items are **driven by the `menus` table** (see schema in section 3) so admins can reorder/rename/hide items without code changes; each item respects `permission_code` visibility.
- **Menu items can also appear in the Navbar** — controlled per item via `show_in_navbar` (e.g. quick links to "Users" or a profile dropdown), independent from whether it's shown in the sidebar.
- Frontend build: npm with `tailwindcss` + `daisyui` plugin; `tailwind.config.js` scans `app/templates/**/*.html`.
- Suggested daisyUI components: `navbar`, `drawer` (sidebar), `menu` (nested sidebar list with a collapsible submenu for 1-level children), `table`, `modal` (quick create/edit forms), `alert` (flash messages), `badge` (active/inactive status, role name).
- Build command: `npm run build:css` (watch mode during development).

---

## 10. Docker & Deployment

- `Dockerfile`: multi-stage — build Tailwind assets, then copy into the final Python image (gunicorn).
- `docker-compose.yml` with 2 minimal services:
  - `web` (Flask + gunicorn)
  - `db` (postgres:16, with a persistent volume)
- `.env` for `DATABASE_URL`, `SECRET_KEY`, `FLASK_ENV`.
- Database migrations run automatically on container start (`flask db upgrade`) or via an entrypoint script.

---

## 11. Step-by-Step Prompts for GitHub Copilot

Follow this order so Copilot builds incrementally and keeps context clear:

1. **Scaffold the project** — create the folder structure above + `config.py` (dev/prod config) + app factory `create_app()`.
2. **Set up extensions** — `db`, `login_manager`, `migrate`, `csrf` in `extensions.py`, registered in the app factory.
3. **Create models** — `User`, `Role`, `Permission`, `role_permissions` (association table), `Menu`, `ActivityLog`.
4. **Set up Flask-Migrate** — init migrations, generate the first migration.
5. **Create seed scripts** — Super Admin + base permissions + Admin role + default menu tree (`seed_menu.py`).
6. **Auth blueprint** — `login`/`logout` routes only (no register), login form with Flask-WTF.
7. **Decorators & helpers** — `@permission_required`, `@role_required`, `log_activity()`, `menu_builder()` (builds the nested-menu tree, max 1 level, filtered by user permissions).
8. **Base layout** — `base.html` with Navbar + Sidebar partials, wired to `menu_builder()`.
9. **Main blueprint** — home page/dashboard.
10. **Users blueprint** — list, create, **dedicated edit-user page**, toggle active, reset password.
11. **Roles blueprint** — role CRUD + permission assignment.
12. **Logs blueprint** — activity log view page + filters.
13. **TailwindCSS + daisyUI setup** — `package.json`, `tailwind.config.js`, navbar/sidebar templates with nested menu rendering.
14. **Dockerize** — `Dockerfile`, `docker-compose.yml`, `.env.example`.
15. **Basic tests** — smoke test for login and permission-gated page access.

---

## 12. Things to Decide Before Coding

- [ ] Database name & default credentials for `.env.example`
- [ ] Integer autoincrement `id` vs `UUID` for primary keys (UUID recommended if endpoints are public-facing)
- [ ] Force password change on first login (since accounts start with an admin-set password)?
- [ ] Password reset policy: admin sets it manually, or an email-based reset link later?
- [ ] Exact default menu tree (which items go under "Management", which are top-level)

---

_This plan is meant as context/prompt material for GitHub Copilot. You can copy-paste sections (e.g. the folder structure + step-by-step prompts) into Copilot Chat as initial instructions._
