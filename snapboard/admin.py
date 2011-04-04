from snapboard import models as smodels
from django.contrib import admin

class CategoryAdmin(admin.ModelAdmin):
    prepopulated_fields = {"slug": ("name",)}

class ThreadAdmin(admin.ModelAdmin):
    list_display = ('user', 'name', 'category', 'sticky', 'private', 'closed')
    list_filter = ('closed', 'sticky', 'category', 'private',)
    search_fields = ('name',)
    raw_id_fields = ('user', 'category')

class PostAdmin(admin.ModelAdmin):
    list_display = ('user', 'date', 'thread', 'ip')
    search_fields = ('text', 'user')
    raw_id_fields = ('thread', 'user',)


admin.site.register(smodels.Category, CategoryAdmin)
admin.site.register(smodels.Post, PostAdmin)
admin.site.register(smodels.Thread, ThreadAdmin)
#admin.site.register(smodels.WatchList, WatchListAdmin)
admin.site.register(smodels.UserSettings)
