import 'package:flutter/material.dart';
import '../services/permission_service.dart';
import '../main.dart';

class PermissionScreen extends StatefulWidget {
  /// true면 초기 설정 단계(완료 버튼으로 대시보드 이동), false면 설정 화면 내 탭
  final bool isSetup;
  final VoidCallback? onDone;

  const PermissionScreen({super.key, this.isSetup = false, this.onDone});

  @override
  State<PermissionScreen> createState() => _PermissionScreenState();
}

class _PermissionScreenState extends State<PermissionScreen>
    with WidgetsBindingObserver {
  bool _listenerEnabled = false;
  bool _batteryIgnored = false;
  bool _notifGranted = false;
  bool _wsRunning = false;
  bool _loading = true;

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addObserver(this);
    _checkAll();
  }

  @override
  void dispose() {
    WidgetsBinding.instance.removeObserver(this);
    super.dispose();
  }

  // 앱이 포그라운드로 돌아올 때 권한 재확인 (설정 화면에서 돌아온 경우)
  @override
  void didChangeAppLifecycleState(AppLifecycleState state) {
    if (state == AppLifecycleState.resumed) {
      _checkAll();
    }
  }

  Future<void> _checkAll() async {
    setState(() => _loading = true);
    final results = await Future.wait([
      PermissionService.isNotificationListenerEnabled(),
      PermissionService.isBatteryOptimizationIgnored(),
      PermissionService.isNotificationPermissionGranted(),
      PermissionService.isWsServiceRunning(),
    ]);
    if (mounted) {
      setState(() {
        _listenerEnabled = results[0];
        _batteryIgnored = results[1];
        _notifGranted = results[2];
        _wsRunning = results[3];
        _loading = false;
      });
    }
  }

  bool get _allGranted =>
      _listenerEnabled && _batteryIgnored && _notifGranted && _wsRunning;

  Future<void> _grantAll() async {
    if (!_notifGranted) {
      await PermissionService.requestNotificationPermission();
      await _checkAll();
    }
    if (!_batteryIgnored) {
      await PermissionService.requestIgnoreBatteryOptimizations();
      await _checkAll();
    }
    if (!_listenerEnabled) {
      await PermissionService.openNotificationListenerSettings();
      // 돌아오면 didChangeAppLifecycleState에서 재확인
    }
    if (!_wsRunning) {
      await PermissionService.startWsService();
      await Future.delayed(const Duration(seconds: 1));
      await _checkAll();
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('권한 설정')),
      body: _buildBody(),
    );
  }

  Widget _buildBody() {
    if (_loading) {
      return const Center(child: CircularProgressIndicator());
    }

    return SingleChildScrollView(
      padding: const EdgeInsets.all(20),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          // 안내 박스
          Container(
            padding: const EdgeInsets.all(14),
            decoration: BoxDecoration(
              color: Colors.blue.shade50,
              borderRadius: BorderRadius.circular(12),
              border: Border.all(color: Colors.blue.shade200),
            ),
            child: Row(
              children: [
                Icon(Icons.info_outline, color: Colors.blue.shade700),
                const SizedBox(width: 10),
                const Expanded(
                  child: Text(
                    '카카오톡·안전신문고 알림에서 신고번호를 자동 감지하고,\n크롤링 완료 결과를 알림으로 받으려면\n아래 권한이 모두 필요합니다.',
                    style: TextStyle(fontSize: 13, height: 1.5),
                  ),
                ),
              ],
            ),
          ),
          const SizedBox(height: 20),

          // 권한 카드들
          _PermCard(
            icon: Icons.notifications_active,
            title: '알림 접근 권한',
            desc: '카카오톡·안전신문고 알림에서 신고번호를 자동으로 감지합니다.\n시스템 설정에서 직접 허용해야 합니다.',
            granted: _listenerEnabled,
            grantedLabel: '활성화됨',
            deniedLabel: '허용 안 됨',
            onGrant: () async {
              await PermissionService.openNotificationListenerSettings();
              // 돌아왔을 때 didChangeAppLifecycleState에서 재확인
            },
            buttonLabel: '알림 접근 허용하기',
          ),
          const SizedBox(height: 12),

          _PermCard(
            icon: Icons.battery_full,
            title: '배터리 최적화 제외',
            desc: '백그라운드에서 알림을 지속적으로 감지하려면\n배터리 최적화에서 제외되어야 합니다.',
            granted: _batteryIgnored,
            grantedLabel: '제외됨',
            deniedLabel: '최적화 대상',
            onGrant: () async {
              await PermissionService.requestIgnoreBatteryOptimizations();
              await _checkAll();
            },
            buttonLabel: '배터리 최적화 제외 요청',
          ),
          const SizedBox(height: 12),

          _PermCard(
            icon: Icons.circle_notifications,
            title: '알림 표시 권한 (Android 13+)',
            desc: '크롤링 완료 후 처리 결과를 팝업 알림으로 받으려면\n알림 표시 권한이 필요합니다.',
            granted: _notifGranted,
            grantedLabel: '허용됨',
            deniedLabel: '거부됨',
            onGrant: () async {
              await PermissionService.requestNotificationPermission();
              await _checkAll();
            },
            buttonLabel: '알림 권한 요청',
          ),
          const SizedBox(height: 12),

          _PermCard(
            icon: Icons.wifi_tethering,
            title: '백그라운드 서버 연결 (WebSocket)',
            desc: '앱을 종료해도 서버의 크롤링 이벤트를 실시간으로 받으려면\n백그라운드 서비스를 시작해야 합니다.\n상단 상태바에 지속 알림이 표시됩니다.',
            granted: _wsRunning,
            grantedLabel: '실행 중',
            deniedLabel: '중지됨',
            onGrant: () async {
              await PermissionService.startWsService();
              await Future.delayed(const Duration(seconds: 1));
              await _checkAll();
            },
            buttonLabel: '백그라운드 서비스 시작',
          ),
          const SizedBox(height: 28),

          // 일괄 허용 버튼
          if (!_allGranted) ...[
            FilledButton.icon(
              icon: _loading
                  ? const SizedBox(
                      width: 16, height: 16,
                      child: CircularProgressIndicator(strokeWidth: 2, color: Colors.white),
                    )
                  : const Icon(Icons.shield_outlined, size: 18),
              label: const Text('모든 권한 한 번에 허용하기'),
              style: FilledButton.styleFrom(
                  padding: const EdgeInsets.symmetric(vertical: 14)),
              onPressed: _loading ? null : _grantAll,
            ),
            const SizedBox(height: 16),
          ],

          // 전체 상태
          AnimatedContainer(
            duration: const Duration(milliseconds: 300),
            padding: const EdgeInsets.all(14),
            decoration: BoxDecoration(
              color: _allGranted
                  ? Colors.green.shade50
                  : Colors.orange.shade50,
              borderRadius: BorderRadius.circular(12),
              border: Border.all(
                color: _allGranted
                    ? Colors.green.shade300
                    : Colors.orange.shade300,
              ),
            ),
            child: Row(
              children: [
                Icon(
                  _allGranted ? Icons.check_circle : Icons.warning_amber,
                  color: _allGranted
                      ? Colors.green.shade700
                      : Colors.orange.shade700,
                ),
                const SizedBox(width: 10),
                Expanded(
                  child: Text(
                    _allGranted
                        ? '모든 권한이 허용되었습니다. 알림 자동 처리가 활성화됩니다.'
                        : '일부 권한이 허용되지 않아 자동 알림 기능이 제한될 수 있습니다.',
                    style: TextStyle(
                      fontSize: 13,
                      color: _allGranted
                          ? Colors.green.shade800
                          : Colors.orange.shade800,
                    ),
                  ),
                ),
              ],
            ),
          ),

          if (widget.isSetup) ...[
            const SizedBox(height: 20),
            FilledButton(
              onPressed: () {
                if (widget.onDone != null) {
                  widget.onDone!();
                } else {
                  Navigator.of(context).pushAndRemoveUntil(
                    MaterialPageRoute(
                        builder: (_) => const MainNavigationScreen()),
                    (_) => false,
                  );
                }
              },
              style: FilledButton.styleFrom(
                  padding: const EdgeInsets.symmetric(vertical: 14)),
              child: Text(_allGranted ? '완료' : '나중에 설정하기'),
            ),
          ],
          const SizedBox(height: 12),
          Center(
            child: TextButton.icon(
              icon: const Icon(Icons.refresh, size: 16),
              label: const Text('권한 상태 새로고침'),
              onPressed: _checkAll,
            ),
          ),
        ],
      ),
    );
  }
}

class _PermCard extends StatelessWidget {
  final IconData icon;
  final String title;
  final String desc;
  final bool granted;
  final String grantedLabel;
  final String deniedLabel;
  final VoidCallback onGrant;
  final String buttonLabel;

  const _PermCard({
    required this.icon,
    required this.title,
    required this.desc,
    required this.granted,
    required this.grantedLabel,
    required this.deniedLabel,
    required this.onGrant,
    required this.buttonLabel,
  });

  @override
  Widget build(BuildContext context) {
    final color = granted ? Colors.green : Colors.red;

    return Card(
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              children: [
                Icon(icon,
                    color: Theme.of(context).colorScheme.primary, size: 22),
                const SizedBox(width: 8),
                Expanded(
                  child: Text(title,
                      style: const TextStyle(
                          fontWeight: FontWeight.bold, fontSize: 15)),
                ),
                Container(
                  padding:
                      const EdgeInsets.symmetric(horizontal: 8, vertical: 3),
                  decoration: BoxDecoration(
                    color: color.withOpacity(0.1),
                    borderRadius: BorderRadius.circular(20),
                    border: Border.all(color: color.withOpacity(0.4)),
                  ),
                  child: Row(
                    mainAxisSize: MainAxisSize.min,
                    children: [
                      Icon(
                          granted ? Icons.check_circle : Icons.cancel,
                          size: 12,
                          color: color),
                      const SizedBox(width: 4),
                      Text(
                        granted ? grantedLabel : deniedLabel,
                        style: TextStyle(
                            fontSize: 11,
                            color: color,
                            fontWeight: FontWeight.bold),
                      ),
                    ],
                  ),
                ),
              ],
            ),
            const SizedBox(height: 8),
            Text(desc,
                style:
                    const TextStyle(color: Colors.grey, fontSize: 12, height: 1.5)),
            if (!granted) ...[
              const SizedBox(height: 12),
              SizedBox(
                width: double.infinity,
                child: OutlinedButton.icon(
                  icon: const Icon(Icons.open_in_new, size: 16),
                  label: Text(buttonLabel,
                      style: const TextStyle(fontSize: 13)),
                  onPressed: onGrant,
                ),
              ),
            ],
          ],
        ),
      ),
    );
  }
}
