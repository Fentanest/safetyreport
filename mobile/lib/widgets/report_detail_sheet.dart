import 'dart:async';
import 'dart:io';
import 'package:flutter/material.dart';
import 'package:http/http.dart' as http;
import 'package:open_filex/open_filex.dart';
import 'package:path_provider/path_provider.dart';
import 'package:url_launcher/url_launcher.dart';
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

  /// 처리내용에서 전화번호 추출
  /// ☎/☏/📞 아이콘, 전화/Tel/TEL 키워드, 또는 한국 전화번호 형식 자체 감지
  String? _extractPhone(String text) {
    // 1순위: 전화 아이콘/키워드 뒤 번호
    final iconMatch = RegExp(
      r'(?:[☎☏📞]|전화\s*번호\s*[:：]?|[Tt][Ee][Ll]\s*[:：]?)\s*([0-9][0-9\-\s]{6,14}[0-9])',
    ).firstMatch(text);
    if (iconMatch != null) {
      return iconMatch.group(1)!.replaceAll(RegExp(r'[\s]'), '');
    }
    // 2순위: 한국 전화번호 형식 자체 (02-, 03x-, 04x-, 05x-, 07x-, 010-, 011-, 016-, 017-, 018-, 019-)
    final numMatch = RegExp(
      r'\b(0(?:2|[3-9]\d)[\-\s]\d{3,4}[\-\s]\d{4}|01[016789][\-\s]\d{3,4}[\-\s]\d{4})\b',
    ).firstMatch(text);
    if (numMatch != null) {
      return numMatch.group(1)!.replaceAll(RegExp(r'[\s]'), '');
    }
    return null;
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
    final path = Uri.tryParse(url)?.path.toLowerCase() ?? url.toLowerCase();
    return path.endsWith('.mp4') || path.endsWith('.mov') ||
        path.endsWith('.avi') || path.endsWith('.webm') ||
        path.endsWith('.mkv');
  }

  bool _isImage(String url) {
    final path = Uri.tryParse(url)?.path.toLowerCase() ?? url.toLowerCase();
    return path.endsWith('.jpg') || path.endsWith('.jpeg') ||
        path.endsWith('.png') || path.endsWith('.gif') ||
        path.endsWith('.webp');
  }

  @override
  Widget build(BuildContext context) {
    final color = _statusColor(report.status);
    final photos = _splitUrls(report.attachedPhotos);
    final files = _splitUrls(report.attachedFiles);
    final mapUrls = _splitUrls(report.mapImage);

    // 지도 이미지 → imageUrls 맨 앞에 추가
    final imageUrls = <String>[];
    for (final u in mapUrls) {
      if (!imageUrls.contains(u)) imageUrls.add(u);
    }
    // 첨부사진: 확장자와 무관하게 모두 이미지로 취급 (사진 컬럼이므로)
    // 단, 동영상 확장자가 명확한 경우엔 videoUrls로 분류
    final videoUrls = <String>[];
    for (final u in photos) {
      if (_isVideo(u)) {
        if (!videoUrls.contains(u)) videoUrls.add(u);
      } else {
        if (!imageUrls.contains(u)) imageUrls.add(u);
      }
    }
    // 파일 중 이미지/동영상/기타 분류
    for (final u in files) {
      if (_isImage(u) && !imageUrls.contains(u)) imageUrls.add(u);
      else if (_isVideo(u) && !videoUrls.contains(u)) videoUrls.add(u);
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
              Builder(builder: (ctx) {
                final phone = _extractPhone(report.processContent);
                if (phone == null) return const SizedBox.shrink();
                return Padding(
                  padding: const EdgeInsets.only(top: 8),
                  child: OutlinedButton.icon(
                    icon: const Icon(Icons.phone, size: 16),
                    label: Text('전화걸기  $phone'),
                    onPressed: () async {
                      final uri = Uri.parse('tel:$phone');
                      if (await canLaunchUrl(uri)) {
                        await launchUrl(uri);
                      }
                    },
                  ),
                );
              }),
            ],
            // 인라인 이미지
            if (imageUrls.isNotEmpty) ...[
              const SizedBox(height: 12),
              _sectionLabel('첨부 사진'),
              const SizedBox(height: 8),
              ...imageUrls.map((url) => Padding(
                padding: const EdgeInsets.only(bottom: 8),
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.stretch,
                  children: [
                    _RetryableImage(url: url),
                    const SizedBox(height: 4),
                    OutlinedButton.icon(
                      icon: const Icon(Icons.open_in_new, size: 14),
                      label: const Text('다른 앱으로 열기', style: TextStyle(fontSize: 12)),
                      style: OutlinedButton.styleFrom(
                        visualDensity: VisualDensity.compact,
                        padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 4),
                      ),
                      onPressed: () => _openExternal(context, url),
                    ),
                  ],
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
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.stretch,
                  children: [
                    _VideoPlayer(url: url),
                    const SizedBox(height: 4),
                    OutlinedButton.icon(
                      icon: const Icon(Icons.open_in_new, size: 14),
                      label: const Text('다른 앱으로 열기', style: TextStyle(fontSize: 12)),
                      style: OutlinedButton.styleFrom(
                        visualDensity: VisualDensity.compact,
                        padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 4),
                      ),
                      onPressed: () => _openExternal(context, url),
                    ),
                  ],
                ),
              )),
            ],
            // 기타 첨부파일 — 인라인 동영상 재생 시도
            if (otherFiles.isNotEmpty) ...[
              const SizedBox(height: 12),
              _sectionLabel('첨부파일'),
              const SizedBox(height: 4),
              ...otherFiles.asMap().entries.map((e) {
                final url = e.value;
                final idx = e.key + 1;
                final fileName = Uri.tryParse(url)?.pathSegments
                    .where((s) => s.isNotEmpty)
                    .lastOrNull ?? '첨부파일 $idx';
                return Padding(
                  padding: const EdgeInsets.only(bottom: 8),
                  child: _VideoPlayer(url: url, label: fileName),
                );
              }),
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

  /// 파일을 캐시에 다운로드한 뒤 Android ACTION_VIEW로 다른 앱에 전달
  Future<void> _openExternal(BuildContext context, String url) async {
    final uri = Uri.tryParse(url);
    if (uri == null) return;

    // 파일명 추출 (쿼리스트링 제거)
    final fileName = uri.pathSegments.lastWhere(
      (s) => s.isNotEmpty,
      orElse: () => 'file_${DateTime.now().millisecondsSinceEpoch}',
    );

    ScaffoldMessenger.of(context).showSnackBar(
      const SnackBar(
        content: Text('파일 불러오는 중...'),
        duration: Duration(seconds: 10),
      ),
    );

    try {
      final dir = await getTemporaryDirectory();
      final file = File('${dir.path}/$fileName');

      // 이미 캐시에 있으면 재사용
      if (!await file.exists()) {
        final response = await http.get(uri);
        await file.writeAsBytes(response.bodyBytes);
      }

      if (context.mounted) {
        ScaffoldMessenger.of(context).hideCurrentSnackBar();
      }

      final result = await OpenFilex.open(file.path);
      if (result.type != ResultType.done && context.mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(content: Text('열 수 있는 앱이 없습니다: ${result.message}')),
        );
      }
    } catch (e) {
      if (context.mounted) {
        ScaffoldMessenger.of(context).hideCurrentSnackBar();
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(content: Text('파일을 열지 못했습니다: $e')),
        );
      }
    }
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
/// 로드 실패 시 2초 후 자동 1회 재시도하는 이미지 위젯
class _RetryableImage extends StatefulWidget {
  final String url;
  const _RetryableImage({required this.url});

  @override
  State<_RetryableImage> createState() => _RetryableImageState();
}

class _RetryableImageState extends State<_RetryableImage> {
  static const _maxAutoRetry = 5;
  static const _retryDelay = Duration(seconds: 1);

  int _attempt = 0;
  bool _failed = false;   // 최종 실패 (수동 버튼 표시)
  bool _retrying = false; // 재시도 대기 중 (스피너 표시)
  Timer? _retryTimer;

  @override
  void dispose() {
    _retryTimer?.cancel();
    super.dispose();
  }

  void _onError() {
    if (!mounted) return;
    if (_attempt < _maxAutoRetry) {
      setState(() => _retrying = true);
      _retryTimer = Timer(_retryDelay, () {
        if (!mounted) return;
        setState(() {
          _attempt++;
          _retrying = false;
        });
      });
    } else {
      setState(() { _failed = true; _retrying = false; });
    }
  }

  void _manualRetry() {
    if (!mounted) return;
    setState(() { _attempt++; _failed = false; _retrying = false; });
  }

  @override
  Widget build(BuildContext context) {
    if (_failed) {
      return Container(
        height: 80,
        decoration: BoxDecoration(
          color: Colors.grey.shade100,
          borderRadius: BorderRadius.circular(8),
        ),
        child: Center(
          child: Column(
            mainAxisSize: MainAxisSize.min,
            children: [
              const Icon(Icons.broken_image_outlined, color: Colors.grey),
              TextButton(
                onPressed: _manualRetry,
                child: const Text('다시 시도', style: TextStyle(fontSize: 12)),
              ),
            ],
          ),
        ),
      );
    }

    return ClipRRect(
      borderRadius: BorderRadius.circular(8),
      child: Image.network(
        widget.url,
        key: ValueKey('${widget.url}_$_attempt'),
        fit: BoxFit.cover,
        loadingBuilder: (_, child, progress) {
          if (progress == null) return child; // 로드 완료
          if (_retrying) {
            return Container(
              height: 160,
              color: Colors.grey.shade100,
              child: const Center(child: CircularProgressIndicator(strokeWidth: 2)),
            );
          }
          return Container(
            height: 160,
            color: Colors.grey.shade100,
            child: Center(
              child: CircularProgressIndicator(
                value: progress.expectedTotalBytes != null
                    ? progress.cumulativeBytesLoaded / progress.expectedTotalBytes!
                    : null,
              ),
            ),
          );
        },
        errorBuilder: (_, __, ___) {
          // errorBuilder는 동기적으로 호출되므로 addPostFrameCallback으로 상태 변경
          WidgetsBinding.instance.addPostFrameCallback((_) => _onError());
          return Container(
            height: 80,
            color: Colors.grey.shade100,
            child: const Center(
              child: CircularProgressIndicator(strokeWidth: 2),
            ),
          );
        },
      ),
    );
  }
}

// ──────────────────────────────────────────────────────────────
class _VideoPlayer extends StatefulWidget {
  final String url;
  final String? label;  // 파일명 표시용 (otherFiles에서 사용)
  const _VideoPlayer({required this.url, this.label});

  @override
  State<_VideoPlayer> createState() => _VideoPlayerState();
}

class _VideoPlayerState extends State<_VideoPlayer> {
  late VideoPlayerController _ctrl;
  bool _initialized = false;
  bool _error = false;
  bool _seeking = false;
  bool _wasPlaying = false;
  Duration _seekPosition = Duration.zero;

  @override
  void initState() {
    super.initState();
    _initController();
  }

  void _initController() {
    _ctrl = VideoPlayerController.networkUrl(Uri.parse(widget.url))
      ..initialize().then((_) {
        if (mounted) setState(() => _initialized = true);
      }).catchError((_) {
        if (mounted) setState(() => _error = true);
      });
  }

  @override
  void dispose() {
    _ctrl.dispose();
    super.dispose();
  }

  String _fmt(Duration d) {
    final m = d.inMinutes.remainder(60).toString().padLeft(2, '0');
    final s = d.inSeconds.remainder(60).toString().padLeft(2, '0');
    return '$m:$s';
  }

  @override
  Widget build(BuildContext context) {
    if (_error) {
      return Container(
        padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 10),
        decoration: BoxDecoration(
          color: Colors.grey.shade100,
          borderRadius: BorderRadius.circular(8),
        ),
        child: Row(
          children: [
            const Icon(Icons.videocam_off_outlined, color: Colors.grey, size: 20),
            const SizedBox(width: 10),
            Expanded(
              child: Text(
                widget.label ?? '동영상',
                style: const TextStyle(fontSize: 13, color: Colors.grey),
                maxLines: 1, overflow: TextOverflow.ellipsis,
              ),
            ),
            TextButton(
              style: TextButton.styleFrom(visualDensity: VisualDensity.compact),
              onPressed: () {
                if (mounted) setState(() { _error = false; _initialized = false; _seeking = false; _seekPosition = Duration.zero; });
                _ctrl.dispose();
                _initController();
              },
              child: const Text('재시도', style: TextStyle(fontSize: 12)),
            ),
          ],
        ),
      );
    }
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
        alignment: Alignment.bottomCenter,
        children: [
          AspectRatio(
            aspectRatio: _ctrl.value.aspectRatio,
            child: GestureDetector(
              onTap: () => setState(() {
                _ctrl.value.isPlaying ? _ctrl.pause() : _ctrl.play();
              }),
              child: VideoPlayer(_ctrl),
            ),
          ),
          // 하단 컨트롤 바
          ValueListenableBuilder<VideoPlayerValue>(
            valueListenable: _ctrl,
            builder: (_, value, __) {
              final pos = _seeking ? _seekPosition : value.position;
              final dur = value.duration;
              final maxMs = dur.inMilliseconds > 0 ? dur.inMilliseconds.toDouble() : 1.0;
              final posMs = pos.inMilliseconds.toDouble().clamp(0.0, maxMs);
              return Container(
                color: Colors.black54,
                padding: const EdgeInsets.only(left: 4, right: 8, bottom: 2),
                child: Row(
                  children: [
                    // 재생/일시정지 버튼
                    IconButton(
                      padding: EdgeInsets.zero,
                      constraints: const BoxConstraints(minWidth: 36, minHeight: 36),
                      icon: Icon(
                        value.isPlaying ? Icons.pause : Icons.play_arrow,
                        color: Colors.white, size: 22,
                      ),
                      onPressed: () => setState(() {
                        value.isPlaying ? _ctrl.pause() : _ctrl.play();
                      }),
                    ),
                    // 현재 위치
                    Text(_fmt(pos),
                        style: const TextStyle(color: Colors.white, fontSize: 11)),
                    // 시크 바
                    Expanded(
                      child: SliderTheme(
                        data: SliderTheme.of(context).copyWith(
                          thumbShape: const RoundSliderThumbShape(enabledThumbRadius: 6),
                          overlayShape: const RoundSliderOverlayShape(overlayRadius: 12),
                          trackHeight: 2,
                          activeTrackColor: Colors.white,
                          inactiveTrackColor: Colors.white30,
                          thumbColor: Colors.white,
                          overlayColor: Colors.white24,
                        ),
                        child: Slider(
                          value: posMs,
                          min: 0,
                          max: maxMs,
                          onChangeStart: (_) {
                            _wasPlaying = value.isPlaying;
                            if (_wasPlaying) _ctrl.pause();
                            setState(() { _seeking = true; _seekPosition = pos; });
                          },
                          onChanged: (v) => setState(() =>
                              _seekPosition = Duration(milliseconds: v.toInt())),
                          onChangeEnd: (v) {
                            _ctrl.seekTo(Duration(milliseconds: v.toInt()));
                            if (_wasPlaying) _ctrl.play();
                            setState(() => _seeking = false);
                          },
                        ),
                      ),
                    ),
                    // 전체 길이
                    Text(_fmt(dur),
                        style: const TextStyle(color: Colors.white70, fontSize: 11)),
                  ],
                ),
              );
            },
          ),
        ],
      ),
    );
  }
}
