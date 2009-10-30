from datetime import datetime

from django.conf import settings
from django.contrib.auth.models import User
from django.core.exceptions import PermissionDenied
from django.core.mail import send_mail
from django.core.urlresolvers import reverse
from django.db import models, connection
from django.db.models import signals, Q
from django.utils.translation import ugettext_lazy as _

try:
    from notification import models as notification
except ImportError:
    notification = None

from snapboard import managers
from snapboard.middleware import threadlocals

__all__ = [
    'SNAP_PREFIX', 'SNAP_MEDIA_PREFIX', 'SNAP_POST_FILTER',
    'NOBODY', 'ALL', 'USERS', 'CUSTOM', 'PERM_CHOICES', 'PERM_CHOICES_RESTRICTED',
    'PermissionError', 'is_user_banned', 'is_ip_banned', 
    'Category', 'Invitation', 'Group', 'Thread', 'Post', 'Moderator',
    'WatchList', 'AbuseReport', 'UserSettings', 'IPBan', 'UserBan',
]



SNAP_PREFIX = getattr(settings, 'SNAP_PREFIX', '/snapboard')
SNAP_MEDIA_PREFIX = getattr(settings, 'SNAP_MEDIA_PREFIX', 
        getattr(settings, 'MEDIA_URL', '') + '/snapboard')
SNAP_POST_FILTER = getattr(settings, 'SNAP_POST_FILTER', 'markdown').lower()

NOBODY = 0
ALL = 1
USERS = 2
CUSTOM = 3

PERM_CHOICES = (
    (NOBODY, _('Nobody')),
    (ALL, _('All')),
    (USERS, _('Users')),
    (CUSTOM, _('Custom')),
)

PERM_CHOICES_RESTRICTED = (
    (NOBODY, _('Nobody')),
    (ALL, _('All')),
    (USERS, _('Users')),
    (CUSTOM, _('Custom')),
)

class PermissionError(PermissionDenied):
    """
    Raised when a user tries to perform a forbidden operation, as per the 
    permissions defined by Category objects.
    """
    pass

def is_user_banned(user):
    return user.id in settings.SNAP_BANNED_USERS

def is_ip_banned(ip):
    return ip in settings.SNAP_BANNED_IPS

class Group(models.Model):
    """
    User-administerable group, be used to assign permissions to possibly 
    several users.

    Administrators of the group need to be explicitely added to the users
    list to be considered members.
    """
    name = models.CharField(_('name'), max_length=36)
    users = models.ManyToManyField(User, verbose_name=_('users'), related_name='sb_member_of_group_set')
    admins = models.ManyToManyField(User, verbose_name=_('admins'), related_name='sb_admin_of_group_set') 
    
    class Meta:
        verbose_name = _('group')
        verbose_name_plural = _('groups')

    def __unicode__(self):
        return _('Group "%s"') % self.name

    def has_user(self, user):
        return self.users.filter(pk=user.pk).count() != 0

    def has_admin(self, user):
        return self.admins.filter(pk=user.pk).count() != 0

class Invitation(models.Model):
    """
    Group admins create invitations for users to join their group.

    Staff with site admin access can assign users to groups without
    restriction.
    """
    group = models.ForeignKey(Group, verbose_name=_('group'), related_name='sb_invitation_set')
    sent_by = models.ForeignKey(User, verbose_name=_('sent by'), related_name='sb_sent_invitation_set')
    sent_to = models.ForeignKey(User, verbose_name=_('sent to'), related_name='sb_received_invitation_set')
    sent_date = models.DateTimeField(_('sent date'), auto_now_add=True)
    response_date = models.DateTimeField(_('response date'), blank=True, null=True)
    accepted = models.NullBooleanField(_('accepted'), blank=True)

    class Meta:
        verbose_name = _('invitation')
        verbose_name_plural = _('invitations')

    def __unicode__(self):
        return _('Invitation for "%(group)s" sent by %(sent_by)s to %(sent_to)s.') % {
                'group': self.group.name,
                'sent_by': self.sent_by,
                'sent_to': self.sent_to }

    def notify_received(instance, **kwargs):
        """
        Notifies of new invitations.
        """
        if not notification:
            return
        if instance.accepted is None:
            notification.send(
                [instance.sent_to],
                'group_invitation_received',
                {'invitation': instance})
    notify_received = staticmethod(notify_received)

    def notify_cancelled(instance, **kwargs):
        """
        Notifies of cancelled invitations.
        """
        if not notification:
            return
        if instance.accepted is None:
            notification.send(
                [instance.sent_to],
                'group_invitation_cancelled',
                {'invitation': instance})
    notify_cancelled = staticmethod(notify_cancelled)

signals.post_save.connect(Invitation.notify_received, sender=Invitation)
signals.pre_delete.connect(Invitation.notify_cancelled, sender=Invitation)

class Category(models.Model):
    label = models.CharField(max_length=32, verbose_name=_('label'))
    slug = models.SlugField()

    # Non-private count.
    thread_count = models.IntegerField(default=0)
    # Last _public_ post.
    last_post = models.ForeignKey("snapboard.Post", null=True)

    # last_thread = models.ForeignKey("snapboard.Thread", null=True)
    
    view_perms = models.PositiveSmallIntegerField(_('view permission'), 
        choices=PERM_CHOICES, default=ALL,
        help_text=_('Limits the category\'s visibility.'))
    read_perms = models.PositiveSmallIntegerField(_('read permission'),
        choices=PERM_CHOICES, help_text=_('Limits the ability to read the '\
        'category\'s contents.'), default=ALL)
    post_perms = models.PositiveSmallIntegerField(_('post permission'),
        choices=PERM_CHOICES_RESTRICTED, help_text=_('Limits the ability to '\
        'post in the category.'), default=USERS)
    new_thread_perms = models.PositiveSmallIntegerField(
        _('create thread permission'), choices=PERM_CHOICES_RESTRICTED, 
        help_text=_('Limits the ability to create new threads in the '\
        'category. Only users with permission to post can create new threads,'\
        'unless a group is specified.'), default=USERS)
    
    view_group = models.ForeignKey(Group, verbose_name=_('view group'),
        blank=True, null=True, related_name='can_view_category_set')
    read_group = models.ForeignKey(Group, verbose_name=_('read group'),
        blank=True, null=True, related_name='can_read_category_set')
    post_group = models.ForeignKey(Group, verbose_name=_('post group'),
        blank=True, null=True, related_name='can_post_category_set')
    new_thread_group = models.ForeignKey(Group, verbose_name=_('create thread group'),
        blank=True, null=True, related_name='can_create_thread_category_set')
    
    class Meta:
        verbose_name = _('category')
        verbose_name_plural = _('categories')

    def __unicode__(self):
        return self.label
    
    def moderators(self):
        mods = Moderator.objects.filter(category=self.id)
        if mods.count() > 0:
            return ', '.join([m.user.username for m in mods])
        else:
            return None
    
    def can_view(self, user):
        flag = False
        if self.view_perms == ALL:
            flag = True
        elif self.view_perms == USERS:
            flag = user.is_authenticated()
        elif self.view_perms == CUSTOM:
            flag = user.is_superuser or (user.is_authenticated() and self.view_group.has_user(user))
        return flag
    
    def can_read(self, user):
        flag = False
        if self.read_perms == ALL:
            flag = True
        elif self.read_perms == USERS:
            flag = user.is_authenticated()
        elif self.read_perms == CUSTOM:
            flag = user.is_superuser or (user.is_authenticated() and self.read_group.has_user(user))
        return flag
    
    def can_post(self, user):
        flag = False
        if self.post_perms == USERS:
            flag = user.is_authenticated()
        elif self.post_perms == CUSTOM:
            flag = user.is_superuser or (user.is_authenticated() and self.post_group.has_user(user))
        return flag
    
    def can_create_thread(self, user):
        flag = False
        if self.new_thread_perms == USERS:
            flag = user.is_authenticated()
        elif self.new_thread_perms == CUSTOM:
            flag = user.is_superuser or (user.is_authenticated() and self.new_thread_group.has_user(user))
        return flag
    
    def update_last_post(self):
        """Update the last public post."""
        from snapboard.models import Post
        thread_pks = self.thread_set.exclude(private=True).values_list("pk", flat=True)
        try:
            self.last_post = Post.objects.filter(thread__id__in=thread_pks).order_by("-date")[0]
        except IndexError:
            pass
        else:
            self.save()
        
    def update_thread_count(self, commit=True):
        self.thread_count = self.thread_set.filter(private=False).count()
        if commit:
            self.save()
    
    def update(self):
        self.update_last_post()
        self.update_thread_count()


class Moderator(models.Model):
    category = models.ForeignKey(Category, verbose_name=_('category'))
    user = models.ForeignKey(User, verbose_name=_('user'), related_name='sb_moderator_set')
    
    class Meta:
        verbose_name = _('moderator')
        verbose_name_plural = _('moderators')


class Thread(models.Model):
    subject = models.CharField(max_length=255, verbose_name=_('subject'))
    slug = models.SlugField(max_length=255)
    category = models.ForeignKey(Category, verbose_name=_('category'))
    private = models.BooleanField(default=False, verbose_name=_('private'))
    closed = models.BooleanField(default=False, verbose_name=_('closed'))
    csticky = models.BooleanField(default=False, verbose_name=_('category sticky'))
    gsticky = models.BooleanField(default=False, verbose_name=_('global sticky'))
    
    # Denormalized --- what about just having a link to 'last post'?
    post_count = models.IntegerField(default=0)
    starter = models.CharField(max_length=255)
    starter_email = models.CharField(max_length=255)
    last_poster = models.CharField(max_length=255)
    last_poster_email = models.CharField(max_length=255)
    last_update = models.DateTimeField(null=True)
    
    objects = managers.ThreadManager()#models.Manager() # needs to be explicit due to below
    #    view_manager = managers.ThreadManager()
    
    # created_at
    # updated_at etc.
    
    
    class Meta:
        verbose_name = _('thread')
        verbose_name_plural = _('threads')
    
    def __unicode__(self):
        return self.subject
    
    def get_url(self):
        return reverse('snapboard_thread', args=(self.id,))
    
    def update_post_count(self):
        self.post_count = self.post_set.exclude(censor=True, revision__isnull=False).count()
    
    def update_last_update(self):
        self.last_update = self.get_last_post().date
    
    def update_first_post(self):
        post = self.get_first_post()
        if post is not None:
            self.starter = post.user.username
            self.starter_email = post.user.email
    
    def update_last_post(self):
        post = self.get_last_post()
        if post is not None:
            self.last_poster = post.user.username
            self.last_poster_email = post.user.email
    
    def get_first_post(self):
        try:
            return self.post_set.order_by("date")[0]
        except self.post_set.model.DoesNotExist:
            return None
    
    def get_last_post(self):
        try:
            return self.post_set.order_by("-date")[0]
        except self.post_set.model.DoesNotExist:
            return None
    
    #TODO:
    def get_post_count(self, user, before=None):
        """
        Returns the number of visible posts in the thread or, if ``before`` is 
        a Post object, the number of visible posts in the thread that are
        older.
        """
        qs = self.post_set.filter(revision=None)
        if not user.is_staff:
            qs = qs.exclude(censor=True)
        if before:
            qs.filter(date__lt=before.date)
        return qs.count()
    count_posts = get_post_count
    
    def update(self):
        self.update_post_count()
        self.update_last_update()
        self.update_first_post()
        self.update_last_post()
        self.save()
    
    @staticmethod
    def signal(instance, **kwargs):
        instance.category.update()

signals.post_save.connect(Thread.signal, sender=Thread)
signals.pre_delete.connect(Thread.signal, sender=Thread)


#TODO: Remove the idea of private for posts for private threads.
#TODO: Add link to category?
#TODO: It'd be worth it store the username / email of the poster here.
class Post(models.Model):
    """
    Post objects store information about revisions.

    Both forward and backward revisions are stored as ForeignKeys.
    """
    user = models.ForeignKey(User, editable=False, blank=True, default=None,
        verbose_name=_('user'), related_name='sb_created_posts_set')
    thread = models.ForeignKey(Thread, verbose_name=_('thread'))
    # category = models.ForeignKey(Category ...)
    text = models.TextField(verbose_name=_('text'))
    date = models.DateTimeField(editable=False, auto_now_add=True, verbose_name=_('date'), null=True) 
    ip = models.IPAddressField(verbose_name=_('ip address'), blank=True, null=True)


    odate = models.DateTimeField(editable=False, null=True)
    # (null or ID of post - most recent revision is always a diff of previous)
    revision = models.ForeignKey("self", related_name="rev",
        editable=False, null=True, blank=True)
    previous = models.ForeignKey("self", related_name="prev",
        editable=False, null=True, blank=True)
    # (boolean set by mod.; true if abuse report deemed false)
    censor = models.BooleanField(default=False, verbose_name=_('censored')) # moderator level access
    freespeech = models.BooleanField(default=False, verbose_name=_('protected')) # superuser level access

    #objects = models.Manager() # needs to be explicit due to below
    #view_manager = managers.PostManager()
    objects = managers.PostManager()


    class Meta:
        verbose_name = _('post')
        verbose_name_plural = _('posts')

    def __unicode__(self):
        return u''.join( (unicode(self.user), u': ', unicode(self.date)) )

    def save(self, force_insert=False, force_update=False):
        # TODO: whatever to revisions.
        # TODO: don't notify on revision creation.
        created = self.id is None
        
        if self.previous is not None:
            self.odate = self.previous.odate
        elif created:
            self.odate = datetime.now()

        super(Post, self).save(force_insert, force_update)
        
        # Why would user be none?
        if settings.SNAP_NOTIFY and created and self.user is not None:
            usettings, _ = UserSettings.objects.get_or_create(user=self.user)
            WatchList.objects.get_or_create(user=self.user, thread=self.thread)
            self.notify()
        
        return self
    
    def management_save(self):
        created = self.id is None
    
        if self.previous is not None:
            self.odate = self.previous.odate
        elif created:
            self.odate = datetime.now()
        return super(Post, self).save(False, False)

    def notify(self):
        # TODO: should join the sub. of the thread
        # TODO: should BCC admins
        from snapboard.utils import renders
                
        # Only mail ppl who want emails.
        mail_dict = dict(self.thread.watchlist_set.values_list("user__id", "user__email"))
        dont_mail_pks = UserSettings.objects.filter(user__id__in=mail_dict.keys(), notify_email=False)
        dont_mail_pks = dont_mail_pks.values_list("user__id", flat=True)
        for pk in dont_mail_pks:
            mail_dict.pop(pk)
        
        recipients = set(mail_dict.values())
        recipients.update([t[1] for t in settings.ADMINS])
        
        ctx = {"post": self}
        subj = self.thread.subject
        body = renders("notification/notify_body.txt", ctx)
        
        send_mail(subj, body, settings.DEFAULT_FROM_EMAIL, recipients, fail_silently=settings.DEBUG)
    
    def get_absolute_url(self):
        # Don't know what page this post is on.
        return reverse('snapboard_locate_post', args=(self.id,))
    
    def get_edit_form(self):
        from forms import PostForm
        return PostForm(initial={'post':self.text})
        
    @staticmethod
    def signal(instance, **kwargs):
        instance.thread.update()

signals.post_save.connect(Post.signal, sender=Post)
signals.pre_delete.connect(Post.signal, sender=Post)


class AbuseReport(models.Model):
    """
    When an abuse report is filed by a registered User, the post is listed
    in this table.

    If the abuse report is rejected as false, the post.freespeech flag can be
    set to disallow further abuse reports on said thread.
    """
    post = models.ForeignKey(Post, verbose_name=_('post'))
    submitter = models.ForeignKey(User, verbose_name=_('submitter'), 
        related_name='sb_abusereport_set')

    class Meta:
        verbose_name = _('abuse report')
        verbose_name_plural = _('abuse reports')
        unique_together = (('post', 'submitter'),)

class WatchList(models.Model):
    """
    Keep track of who is watching what thread.  Notify on change (sidebar).
    """
    user = models.ForeignKey(User, verbose_name=_('user'), related_name='sb_watchlist')
    thread = models.ForeignKey(Thread, verbose_name=_('thread'))
    # no need to be in the admin

class UserSettings(models.Model):
    """
    User data tied to user accounts from the auth module.

    Real name, email, and date joined information are stored in the built-in
    auth module.

    After logging in, save these values in a session variable.
    """
    user = models.OneToOneField(User, unique=True, 
            verbose_name=_('user'), related_name='sb_usersettings')
    ppp = models.IntegerField(
            choices = ((5, '5'), (10, '10'), (20, '20'), (50, '50')),
            default = 20,
            help_text = _('Posts per page'), verbose_name=_('posts per page'))
    tpp = models.IntegerField(
            choices = ((5, '5'), (10, '10'), (20, '20'), (50, '50')),
            default = 20,
            help_text = _('Threads per page'), verbose_name=_('threads per page'))
    notify_email = models.BooleanField(default=True, blank=True,
            help_text = "Receive email notifications about new posts in your threads.", verbose_name=_('notify'))
    reverse_posts = models.BooleanField(
            default=False,
            help_text = _('Display newest posts first.'), verbose_name=_('new posts first'))
    frontpage_filters = models.ManyToManyField(Category,
            null=True, blank=True,
            help_text = _('Filter the list of all topics on these categories.'), verbose_name=_('front page categories'))

    class Meta:
        verbose_name = _('User settings')
        verbose_name_plural = _('User settings')

    def __unicode__(self):
        return _('%s\'s preferences') % self.user
    
class UserBan(models.Model):
    """
    This bans the user from posting messages on the forum. He can still log in.
    """
    user = models.ForeignKey(User, unique=True, verbose_name=_('user'), db_index=True,
            related_name='sb_userban_set',
            help_text=_('The user may still browse the forums anonymously. '
            'Other functions may also still be available to him if he is logged in.'))
    reason = models.CharField(max_length=255, verbose_name=_('reason'),
        help_text=_('This may be displayed to the banned user.'))

    class Meta:
        verbose_name = _('banned user')
        verbose_name_plural = _('banned users')

    def __unicode__(self):
        return _('Banned user: %s') % self.user

    @classmethod
    def update_cache(cls, **kwargs):
        c = connection.cursor()
        c.execute('SELECT user_id FROM %s;' % cls._meta.db_table)
        settings.SNAP_BANNED_USERS = set((x for (x,) in c.fetchall()))

signals.post_save.connect(UserBan.update_cache, sender=UserBan)
signals.post_delete.connect(UserBan.update_cache, sender=UserBan)

class IPBan(models.Model):
    """
    IPs in the list are not allowed to use the boards.
    Only IPv4 addresses are supported, one per record. (patch with IPv6 and/or address range support welcome)
    """
    address = models.IPAddressField(unique=True, verbose_name=_('IP address'), 
            help_text=_('A person\'s IP address may change and an IP address may be '
            'used by more than one person, or by different people over time. '
            'Be careful when using this.'), db_index=True)
    reason = models.CharField(max_length=255, verbose_name=_('reason'),
        help_text=_('This may be displayed to the people concerned by the ban.'))

    class Meta:
        verbose_name = _('banned IP address')
        verbose_name_plural = _('banned IP addresses')
    
    def __unicode__(self):
        return _('Banned IP: %s') % self.address

    @classmethod
    def update_cache(cls, **kwargs):
        c = connection.cursor()
        c.execute('SELECT address FROM %s;' % cls._meta.db_table)
        settings.SNAP_BANNED_IPS = set((x for (x,) in c.fetchall()))

signals.post_save.connect(IPBan.update_cache, sender=IPBan)
signals.post_delete.connect(IPBan.update_cache, sender=IPBan)