"""
统一异常处理
定义业务异常类，便于统一捕获和处理
"""


class BusinessException(Exception):
    """
    业务异常基类
    用于表示可预期的业务错误，区别于系统错误
    """
    def __init__(self, msg: str, code: int = None, status: str = "error"):
        """
        Args:
            msg: 错误消息（用户友好的描述）
            code: 错误码（可选，用于前端识别）
            status: 状态（默认为error）
        """
        super().__init__(msg)
        self.msg = msg
        self.code = code or 500
        self.status = status

    def __str__(self):
        return f"[{self.code}] {self.msg}"

    def to_dict(self):
        """转换为字典格式，便于序列化"""
        return {
            "status": self.status,
            "msg": self.msg,
            "code": self.code
        }
