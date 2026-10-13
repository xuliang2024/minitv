class Copy {
  Copy(this.locale);
  final String locale;

  static const en = {
    'nav.home': 'Home',
    'nav.foryou': 'For You',
    'nav.rewards': 'Rewards',
    'nav.me': 'Me',
    'search': 'Search dramas',
    'retry': 'Could not load. Retry.',
    'empty': 'No more content',
    'play': 'Play',
    'locked': 'Unlock this episode',
    'login': 'Continue with Email',
    'register': 'Create account',
    'code': 'Get code',
    'coins': 'Coins',
    'membership': 'Membership',
    'checkin': 'Check in',
    'ad': 'Watch ad',
    'messages': 'Messages',
    'history': 'History',
    'favorites': 'Favorites',
    'likes': 'Likes',
    'mylist': 'My List',
  };

  static const zh = {
    'nav.home': '首页',
    'nav.foryou': '推荐',
    'nav.rewards': '奖励',
    'nav.me': '我',
    'search': '搜索短剧',
    'retry': '加载失败，请重试',
    'empty': '没有更多了',
    'play': '播放',
    'locked': '解锁本集',
    'login': '使用邮箱继续',
    'register': '注册',
    'code': '获取验证码',
    'coins': '金币',
    'membership': '会员',
    'checkin': '签到',
    'ad': '看广告',
    'messages': '消息',
    'history': '历史',
    'favorites': '收藏',
    'likes': '点赞',
    'mylist': '追剧',
  };

  String t(String key) => (locale == 'zh' ? zh : en)[key] ?? en[key] ?? key;
}
