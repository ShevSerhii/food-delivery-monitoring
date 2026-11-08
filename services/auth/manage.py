"""Керування користувачами з командного рядка.

    docker compose exec auth python manage.py list
    docker compose exec auth python manage.py add olena --role viewer
    docker compose exec auth python manage.py delete olena
"""

import argparse
import getpass
import sys

import storage


def main():
    parser = argparse.ArgumentParser(description="Користувачі системи моніторингу")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("list", help="показати всіх користувачів")

    add = sub.add_parser("add", help="додати користувача або змінити пароль")
    add.add_argument("username")
    add.add_argument("--role", choices=storage.ROLES, default="viewer")
    add.add_argument("--password", help="якщо не вказано - буде запитано")

    delete = sub.add_parser("delete", help="видалити користувача")
    delete.add_argument("username")

    args = parser.parse_args()
    storage.init_db()

    if args.command == "list":
        for user in storage.list_users():
            print(f"{user['username']:<20} {user['role']:<8} {user['created_at']}")
    elif args.command == "add":
        password = args.password or getpass.getpass("Пароль: ")
        if len(password) < 6:
            sys.exit("пароль має бути не коротшим за 6 символів")
        storage.save_user(args.username, password, args.role)
        print(f"користувача {args.username} збережено ({args.role})")
    elif args.command == "delete":
        if not storage.delete_user(args.username):
            sys.exit(f"користувача {args.username} не знайдено")
        print(f"користувача {args.username} видалено")


if __name__ == "__main__":
    main()
