# use this if the recovery code is lost too

import getpass

from werkzeug.security import generate_password_hash

import db
import logic


def main():
    db.ensure_database()
    username = input("Username: ").strip().lower()
    if not db.get_user(username):
        print("No account with that username.")
        return
    password = getpass.getpass("New password: ")
    errors = logic.validate_password(password, getpass.getpass("Confirm new password: "))
    if errors:
        print(" ".join(errors.values()))
        return
    code = logic.new_recovery_code()
    db.update_password(username, generate_password_hash(password),
                       generate_password_hash(logic.normalize_code(code)))
    print(f"\nPassword changed for {username}.")
    print(f"New recovery code (write it down): {code}\n")


if __name__ == "__main__":
    main()
