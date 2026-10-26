class DomainError(Exception):
    def __init__(self, code, message, status=400, details=None):
        self.code,self.message,self.status,self.details=code,message,status,details
