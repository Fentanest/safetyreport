import 'package:flutter/material.dart';
import 'package:url_launcher/url_launcher.dart';
import 'package:cached_network_image/cached_network_image.dart';
import 'package:video_player/video_player.dart';
import '../models/report.dart';

void showReportDetailSheet(BuildContext context, Report report) {
  showModalBottomSheet(
    context: context,
    isScrollControlled: true,
    shape: const RoundedRectangleBorder(
      borderRadius: BorderRadius.vertical(top: Radius.circular(20)),
    ),
    builder: (_) => ReportDetailSheet(report: report),
  );
}

class ReportDetailSheet extends StatelessWidget {
  final Report report;
  const ReportDetailSheet({super.key, required this.report});

  Color _statusColor(String s) {
    if (s == '일부수용') return const Color(0xFF43A047);
    if (s.contains('수용') && !s.contains('불')) return Colors.green;
    if (s.contains('불수용')) return Colors.red;
    if (s.contains('처리') || s.contains('진행')) return Colors.orange;
    return Colors.grey;
  }

  Future<void> _openInSafetyApp(BuildContext context) async {
    if (report.id.isEmpty) {
      ScaffoldMessenger.of(context).showSnackBar(
        const SnackBar(content: Text('신고 ID 정보가 없습니다.')));
      return;
    }
    final uri = Uri.parse(
        'appsafetyreport://view?c_no=${report.id}&ext_path=M_MY_01_S0002.html&mem_yn=Y');
    try {
      final launched = await launchUrl(uri, mode: LaunchMode.externalApplication);
      if (!launched && context.mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          const SnackBar(content: Text('안전신문고 앱이 설치되어 있지 않습니다.')));
      }
    } catch (e) {
      if (context.mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          const SnackBar(content: Text('안전신문고 앱이 설치되어 있지 않습니다.')));
      }
    }
  }

  List<String> _splitUrls(String raw) {
    if (raw.isEmpty || raw == '6개월 초과') return [];
    return raw
        .split(RegExp(r'\n|%0A|%0a'))
        .map((s) => s.trim())
        .where((s) => s.isNotEmpty)
        .toList();
  }

  bool _isVideo(String url) {
    final lower = url.toLowerCase();
    return lower.endsWith('.mp4') || lower.endsWith('.mov') ||
        lower.endsWith('.avi') || lower.endsWith('.webm');
  }

  bool _isImage(String url) {
    final lower = url.toLowerCase();
    return lower.endsWith('.jpg') || lower.endsWith('.jpeg') ||
        lower.endsWith('.png') || lower.endsWith('.gif') ||
        lower.endsWith('.webp');
  }

  @override
  Widget build(BuildContext context) {
    final color = _statusColor(report.status);
    final photos = _splitUrls(report.attachedPhotos);
    final files = _splitUrls(report.attachedFiles);

    // 사진 중 이미지/동영상 분류
    final imageUrls = photos.where((u) => _isImage(u)).toList();
    final videoUrls = photos.where((u) => _isVideo(u)).toList();
    // 파일 중 이미지/동영상/기타 분류
    for (final u in files) {
      if (_isImage(u) && !imageUrls.contains(u)) imageUrls.add(u);
      if (_isVideo(u) && !videoUrls.contains(u)) videoUrls.add(u);
    }
    final otherFiles = files
        .where((u) => !_isImage(u) && !_isVideo(u))
        .toList();

    return DraggableScrollableSheet(
      expand: false,
      initialChildSize: 0.6,
      maxChildSize: 0.95,
      minChildSize: 0.3,
      builder: (_, sc) => Padding(
        padding: const EdgeInsets.fromLTRB(20, 12, 20, 20),
        child: ListView(
          controller: sc,
          children: [
            // 핸들
            Center(
              child: Container(
                width: 40, height: 4,
                margin: const EdgeInsets.only(bottom: 16),
                decoration: BoxDecoration(
                  color: Colors.grey.shade300,
                  borderRadius: BorderRadius.circular(2),
                ),
              ),
            ),
            // 신고명 + 상태칩
            Row(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Expanded(
                  child: Text(
                    report.name,
                    style: const TextStyle(fontSize: 16, fontWeight: FontWeight.bold),
                  ),
                ),
                if (report.status.isNotEmpty) ...[
                  const SizedBox(width: 10),
                  Container(
                    padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 4),
                    decoration: BoxDecoration(
                      color: color.withOpacity(0.1),
                      borderRadius: BorderRadius.circular(20),
                      border: Border.all(color: color.withOpacity(0.4)),
                    ),
                    child: Text(report.status,
                        style: TextStyle(
                            color: color,
                            fontSize: 12,
                            fontWeight: FontWeight.bold)),
                  ),
                ],
              ],
            ),
            const Divider(height: 24),
            // 상세 필드
            if (report.reportNumber.isNotEmpty)
              _field(Icons.tag, '신고번호', report.reportNumber),
            if (report.id.isNotEmpty)
              _field(Icons.fingerprint, '내부 ID', report.id),
            if (report.date.isNotEmpty)
              _field(Icons.calendar_today, '신고일', report.date),
            if (report.responseDate.isNotEmpty)
              _field(Icons.check_circle_outline, '답변일', report.responseDate),
            if (report.agency.isNotEmpty)
              _field(Icons.business, '처리기관', report.agency),
            if (report.manager.isNotEmpty)
              _field(Icons.person_outline, '담당자', report.manager),
            if (report.fineInfo.isNotEmpty && report.fineInfo != '미확인')
              _field(Icons.monetization_on_outlined, '과태료/범칙금', report.fineInfo),
            if (report.penaltyPoints.isNotEmpty)
              _field(Icons.warning_amber_outlined, '벌점', report.penaltyPoints),
            if (report.carNumber.isNotEmpty)
              _field(Icons.directions_car, '차량번호', report.carNumber),
            if (report.law.isNotEmpty)
              _field(Icons.gavel_outlined, '위반법규', report.law),
            if (report.location.isNotEmpty)
              _field(Icons.location_on_outlined, '위반장소', report.location),
            if (report.occurrenceDate.isNotEmpty)
              _field(Icons.event_outlined, '발생일자', report.occurrenceDate +
                  (report.occurrenceTime.isNotEmpty ? '  ${report.occurrenceTime}' : '')),
            if (report.reportContent.isNotEmpty) ...[
              const Divider(height: 20),
              _textBlock('신고내용', report.reportContent),
            ],
            if (report.processContent.isNotEmpty) ...[
              const SizedBox(height: 8),
              _textBlock('처리내용', report.processContent),
            ],
            // 인라인 이미지
            if (imageUrls.isNotEmpty) ...[
              const SizedBox(height: 12),
              _sectionLabel('첨부 사진'),
              const SizedBox(height: 8),
              ...imageUrls.map((url) => Padding(
                padding: const EdgeInsets.only(bottom: 8),
                child: GestureDetector(
                  onTap: () => _openUrl(context, url),
                  child: ClipRRect(
                    borderRadius: BorderRadius.circular(8),
                    child: CachedNetworkImage(
                      imageUrl: url,
                      fit: BoxFit.cover,
                      placeholder: (_, __) => Container(
                        height: 160,
                        color: Colors.grey.shade100,
                        child: const Center(child: CircularProgressIndicator()),
                      ),
                      errorWidget: (_, __, ___) => Container(
                        height: 80,
                        color: Colors.grey.shade100,
                        child: const Center(
                          child: Icon(Icons.broken_image, color: Colors.grey)),
                      ),
                    ),
                  ),
                ),
              )),
            ],
            // 인라인 동영상
            if (videoUrls.isNotEmpty) ...[
              const SizedBox(height: 12),
              _sectionLabel('첨부 동영상'),
              const SizedBox(height: 8),
              ...videoUrls.map((url) => Padding(
                padding: const EdgeInsets.only(bottom: 8),
                child: _VideoPlayer(url: url),
              )),
            ],
            // 기타 첨부파일 링크
            if (otherFiles.isNotEmpty) ...[
              const SizedBox(height: 12),
              _sectionLabel('첨부파일'),
              const SizedBox(height: 4),
              ...otherFiles.asMap().entries.map((e) => ListTile(
                contentPadding: EdgeInsets.zero,
                leading: const Icon(Icons.attach_file, size: 18, color: Colors.blue),
                title: Text('첨부파일 ${e.key + 1}',
                    style: const TextStyle(fontSize: 13, color: Colors.blue,
                        decoration: TextDecoration.underline)),
                onTap: () => _openUrl(context, e.value),
              )),
            ],
            const SizedBox(height: 20),
            // 안전신문고 앱으로 이동
            FilledButton.icon(
              icon: const Icon(Icons.open_in_new, size: 18),
              label: const Text('안전신문고 앱에서 보기'),
              onPressed: () => _openInSafetyApp(context),
            ),
            const SizedBox(height: 4),
            Text(
              '안전신문고 앱이 설치되어 있고 로그인된 상태여야 합니다.',
              textAlign: TextAlign.center,
              style: TextStyle(fontSize: 11, color: Colors.grey.shade500),
            ),
          ],
        ),
      ),
    );
  }

  void _openUrl(BuildContext context, String url) async {
    final uri = Uri.tryParse(url);
    if (uri == null) return;
    try {
      await launchUrl(uri, mode: LaunchMode.externalApplication);
    } catch (_) {}
  }

  Widget _sectionLabel(String text) => Text(text,
      style: TextStyle(
          fontSize: 13,
          fontWeight: FontWeight.bold,
          color: Colors.grey.shade700));

  Widget _textBlock(String label, String value) {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Text(label,
            style: TextStyle(
                fontSize: 12,
                fontWeight: FontWeight.bold,
                color: Colors.grey.shade600)),
        const SizedBox(height: 6),
        Container(
          width: double.infinity,
          padding: const EdgeInsets.all(12),
          decoration: BoxDecoration(
            color: Colors.grey.shade50,
            borderRadius: BorderRadius.circular(8),
            border: Border.all(color: Colors.grey.shade200),
          ),
          child: SelectableText(value,
              style: const TextStyle(fontSize: 13, height: 1.6)),
        ),
      ],
    );
  }

  Widget _field(IconData icon, String label, String value) {
    return Padding(
      padding: const EdgeInsets.only(bottom: 12),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Icon(icon, size: 16, color: Colors.grey.shade600),
          const SizedBox(width: 8),
          SizedBox(
            width: 82,
            child: Text(label,
                style: TextStyle(fontSize: 13, color: Colors.grey.shade600)),
          ),
          Expanded(
            child: Text(value,
                style: const TextStyle(
                    fontSize: 13, fontWeight: FontWeight.w500)),
          ),
        ],
      ),
    );
  }
}

// ──────────────────────────────────────────────────────────────
class _VideoPlayer extends StatefulWidget {
  final String url;
  const _VideoPlayer({required this.url});

  @override
  State<_VideoPlayer> createState() => _VideoPlayerState();
}

class _VideoPlayerState extends State<_VideoPlayer> {
  late VideoPlayerController _ctrl;
  bool _initialized = false;

  @override
  void initState() {
    super.initState();
    _ctrl = VideoPlayerController.networkUrl(Uri.parse(widget.url))
      ..initialize().then((_) {
        if (mounted) setState(() => _initialized = true);
      });
  }

  @override
  void dispose() {
    _ctrl.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    if (!_initialized) {
      return Container(
        height: 160,
        decoration: BoxDecoration(
          color: Colors.black,
          borderRadius: BorderRadius.circular(8),
        ),
        child: const Center(
          child: CircularProgressIndicator(color: Colors.white)),
      );
    }
    return ClipRRect(
      borderRadius: BorderRadius.circular(8),
      child: Stack(
        alignment: Alignment.center,
        children: [
          AspectRatio(
            aspectRatio: _ctrl.value.aspectRatio,
            child: VideoPlayer(_ctrl),
          ),
          GestureDetector(
            onTap: () {
              setState(() {
                _ctrl.value.isPlaying ? _ctrl.pause() : _ctrl.play();
              });
            },
            child: Container(
              color: Colors.transparent,
              child: ValueListenableBuilder(
                valueListenable: _ctrl,
                builder: (_, value, __) => value.isPlaying
                    ? const SizedBox.shrink()
                    : Container(
                        padding: const EdgeInsets.all(12),
                        decoration: BoxDecoration(
                          color: Colors.black54,
                          shape: BoxShape.circle,
                        ),
                        child: const Icon(Icons.play_arrow,
                            color: Colors.white, size: 36),
                      ),
              ),
            ),
          ),
        ],
      ),
    );
  }
}
