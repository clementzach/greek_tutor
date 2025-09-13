import sys
import os
import json
from werkzeug.security import generate_password_hash

BASE_DIR = os.path.dirname(os.path.dirname(__file__))
DATA_DIR = os.path.join(BASE_DIR, 'data')
USERS_PATH = os.path.join(DATA_DIR, 'users.json')


def load_users():
    if not os.path.exists(USERS_PATH):
        return {"users": []}
    with open(USERS_PATH, 'r', encoding='utf-8') as f:
        return json.load(f)


def save_users(data):
    with open(USERS_PATH, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def main():
    if len(sys.argv) != 3:
        print("Usage: python admin/reset_password.py <username> <new_password>")
        sys.exit(1)
    username, new_password = sys.argv[1], sys.argv[2]
    data = load_users()
    for u in data.get('users', []):
        if u.get('username') == username:
            u['password_hash'] = generate_password_hash(new_password)
            save_users(data)
            print(f"Password reset for {username}.")
            return
    print(f"User {username} not found.")


if __name__ == '__main__':
    main()

