class Report {
  final String id;
  final String reportNumber;
  final String name;
  final String date;
  final String responseDate;
  final String agency;
  final String manager;
  final String status;
  final String result;
  final String fineInfo;
  final String penaltyPoints;
  final String carNumber;
  final String law;
  final String location;
  final String occurrenceDate;
  final String occurrenceTime;
  final String reportContent;
  final String processContent;
  final String attachedPhotos;  // 첨부사진 (줄바꿈 구분 URL 목록)
  final String attachedFiles;   // 첨부파일 (줄바꿈 구분 URL 목록)
  final String mapImage;        // 지도 이미지 URL
  final int totalCount;         // 중복차량 전체 신고 횟수
  final int validCount;         // 중복차량 유효 신고 횟수 (취하 제외)

  Report({
    required this.id,
    required this.reportNumber,
    required this.name,
    required this.date,
    required this.responseDate,
    required this.agency,
    required this.manager,
    required this.status,
    required this.result,
    required this.fineInfo,
    required this.penaltyPoints,
    required this.carNumber,
    required this.law,
    required this.location,
    required this.occurrenceDate,
    required this.occurrenceTime,
    required this.reportContent,
    required this.processContent,
    this.attachedPhotos = '',
    this.attachedFiles = '',
    this.mapImage = '',
    this.totalCount = 0,
    this.validCount = 0,
  });

  factory Report.fromJson(Map<String, dynamic> json) {
    return Report(
      id: json['ID']?.toString() ?? '',
      reportNumber: json['신고번호']?.toString() ?? '',
      name: json['신고명']?.toString() ?? '',
      date: json['신고일']?.toString() ?? '',
      responseDate: json['답변일']?.toString() ?? '',
      agency: json['처리기관']?.toString() ?? '',
      manager: json['담당자']?.toString() ?? '',
      status: (json['처리상태'] ?? json['상태'])?.toString() ?? '',
      result: json['결과']?.toString() ?? '',
      fineInfo: json['범칙금_과태료']?.toString() ?? '',
      penaltyPoints: json['벌점']?.toString() ?? '',
      carNumber: json['차량번호']?.toString() ?? '',
      law: json['위반법규']?.toString() ?? '',
      location: json['위반장소']?.toString() ?? '',
      occurrenceDate: json['발생일자']?.toString() ?? '',
      occurrenceTime: json['발생시각']?.toString() ?? '',
      reportContent: json['신고내용']?.toString() ?? '',
      processContent: json['처리내용']?.toString() ?? '',
      attachedPhotos: json['첨부사진']?.toString() ?? '',
      attachedFiles: json['첨부파일']?.toString() ?? '',
      mapImage: json['지도']?.toString() ?? '',
      totalCount: (json['total_count'] as num?)?.toInt() ?? 0,
      validCount: (json['valid_count'] as num?)?.toInt() ?? 0,
    );
  }
}

class DashboardStats {
  final String lastCrawlTime;
  final int total;
  final int acceptCount;
  final int partialCount;
  final int rejectCount;
  final int processingCount;
  final int completedCount;
  final int withdrawCount;
  final int tFineCount;
  final int tPenaltyCount;
  final int tRejectCount;
  final int tUnconfirmedCount;
  final List<Report> recentAnswers;
  final List<Report> watchlist;

  DashboardStats({
    required this.lastCrawlTime,
    required this.total,
    required this.acceptCount,
    required this.partialCount,
    required this.rejectCount,
    required this.processingCount,
    required this.completedCount,
    required this.withdrawCount,
    required this.tFineCount,
    required this.tPenaltyCount,
    required this.tRejectCount,
    required this.tUnconfirmedCount,
    required this.recentAnswers,
    required this.watchlist,
  });

  factory DashboardStats.fromJson(Map<String, dynamic> json) {
    var recentList = json['recent_answers'] as List? ?? [];
    var watchList = json['watchlist'] as List? ?? [];
    return DashboardStats(
      lastCrawlTime: json['last_crawl_time']?.toString() ?? '',
      total: (json['total'] as num?)?.toInt() ?? 0,
      acceptCount: (json['acceptCount'] as num?)?.toInt() ?? 0,
      partialCount: (json['partialCount'] as num?)?.toInt() ?? 0,
      rejectCount: (json['rejectCount'] as num?)?.toInt() ?? 0,
      processingCount: (json['processingCount'] as num?)?.toInt() ?? 0,
      completedCount: (json['completedCount'] as num?)?.toInt() ?? 0,
      withdrawCount: (json['withdrawCount'] as num?)?.toInt() ?? 0,
      tFineCount: (json['tFineCount'] as num?)?.toInt() ?? 0,
      tPenaltyCount: (json['tPenaltyCount'] as num?)?.toInt() ?? 0,
      tRejectCount: (json['tRejectCount'] as num?)?.toInt() ?? 0,
      tUnconfirmedCount: (json['tUnconfirmedCount'] as num?)?.toInt() ?? 0,
      recentAnswers: recentList
          .map((i) => Report.fromJson(i as Map<String, dynamic>))
          .toList(),
      watchlist: watchList
          .map((i) => Report.fromJson(i as Map<String, dynamic>))
          .toList(),
    );
  }
}
