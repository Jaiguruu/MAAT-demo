
class User:
    def __init__(self, username: str):
        self.username = username
        self.id = hash(username) % 1000

class Session:
    def __init__(self, user: User):
        self.user = user
