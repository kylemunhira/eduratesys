from accounts.roles import (
    user_can_manage_customers,
    user_can_manage_users,
    user_is_admin,
    user_role,
    user_sees_all_branches,
)


def roles(request):
    user = request.user
    return {
        "is_admin": user_is_admin(user),
        "can_manage_users": user_can_manage_users(user),
        "can_manage_customers": user_can_manage_customers(user),
        "user_role": user_role(user),
        "sees_all_branches": user_sees_all_branches(user),
    }
