from app.database import recreate_database


if __name__ == "__main__":
    recreate_database()
    print("Database schema recreated.")
