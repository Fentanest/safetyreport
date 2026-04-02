class NotificationItem {
  final String id;
  final String title;
  final String body;
  final String reportNumber;
  final String timestamp;
  final bool isRead;
  /// 크롤링 변경건의 전체 필드 (있을 때만 — 알림 상세에서 ReportDetailSheet 표시용)
  final Map<String, dynamic>? extraData;

  const NotificationItem({
    required this.id,
    required this.title,
    required this.body,
    required this.reportNumber,
    required this.timestamp,
    required this.isRead,
    this.extraData,
  });

  NotificationItem copyWith({bool? isRead}) => NotificationItem(
        id: id,
        title: title,
        body: body,
        reportNumber: reportNumber,
        timestamp: timestamp,
        isRead: isRead ?? this.isRead,
        extraData: extraData,
      );

  factory NotificationItem.fromJson(Map<String, dynamic> json) {
    return NotificationItem(
      id: json['id']?.toString() ?? '',
      title: json['title']?.toString() ?? '',
      body: json['body']?.toString() ?? '',
      reportNumber: json['reportNumber']?.toString() ?? '',
      timestamp: json['timestamp']?.toString() ?? '',
      isRead: json['isRead'] == true,
      extraData: json['extraData'] is Map
          ? Map<String, dynamic>.from(json['extraData'] as Map)
          : null,
    );
  }

  Map<String, dynamic> toJson() => {
        'id': id,
        'title': title,
        'body': body,
        'reportNumber': reportNumber,
        'timestamp': timestamp,
        'isRead': isRead,
        if (extraData != null) 'extraData': extraData,
      };
}
