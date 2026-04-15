from contextvars import ContextVar

request_id_var: ContextVar[str] = ContextVar("request_id", default="-")
conversation_id_var: ContextVar[str] = ContextVar("conversation_id", default="-")
user_id_var: ContextVar[str] = ContextVar("user_id", default="-")


def get_request_id() -> str:
    return request_id_var.get()


def set_request_id(value: str) -> None:
    request_id_var.set(value)


def get_conversation_id() -> str:
    return conversation_id_var.get()


def set_conversation_id(value: str) -> None:
    conversation_id_var.set(value)


def get_user_id() -> str:
    return user_id_var.get()


def set_user_id(value: str) -> None:
    user_id_var.set(value)
