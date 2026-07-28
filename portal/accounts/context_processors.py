from accounts.roles import user_is_admin


def roles(request):
    return {"is_admin": user_is_admin(request.user)}
