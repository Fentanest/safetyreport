class NotificationItem {
  final String id;
  final String title;
  final String body;
  final String reportNumber;
  final String timestamp;
  final bool isRead;

  const NotificationItem({
    required this.id,
    required this.title,
    required this.body,
    required this.reportNumber,
    required this.timestamp,
    required this.isRead,
  });

  NotificationItem copyWith({bool? isRead}) => NotificationItem(
        id: id,
        title: title,
        body: body,
        reportNumber: reportNumber,
        timestamp: timestamp,
        isRead: isRead ?? this.isRead,
      );

  factory NotificationItem.fromJson(Map<String, dynamic> json) {
    return NotificationItem(
      id: json['id']?.toString() ?? '',
      title: json['title']?.toString() ?? '',
      body: json['body']?.toString() ?? '',
      reportNumber: json['reportNumber']?.toString() ?? '',
      timestamp: json['timestamp']?.toString() ?? '',
      isRead: json['isRead'] == true,
    );
  }

  Map<String, dynamic> toJson() => {
        'id': id,
        'title': title,
        'body': body,
        'reportNumber': reportNumber,
        'timestamp': timestamp,
        'isRead': isRead,
      };
}
