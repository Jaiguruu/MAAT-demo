
from models import User
from db import DatabasePool

class AuthService:
    def __init__(self, pool: DatabasePool):
        self.pool = pool

    def login(self, username: str, password: str) -> User:
        user = self.pool.query_user(username)
        if user is None:
            raise ValueError('no user')
        return user

class TokenService:
    def issue(self, user: User) -> str:
        return f'token-{user.id}'

class RateLimiter:
    def allow(self, key: str) -> bool:
        return True
