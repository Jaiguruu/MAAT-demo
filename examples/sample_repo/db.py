
class DatabasePool:
    def query_user(self, username: str):
        return User(username)

    def save_session(self, session):
        return True
