from django.contrib import admin
from application.models.user_models import User, Associate, SportAssociation


@admin.register(User)
class UserAdmin(admin.ModelAdmin):
    list_display = ('user_id', 'username', 'first_name', 'last_name', 'role', 'email')
    list_filter = ('role', )


admin.site.register(Associate)
admin.site.register(SportAssociation)



