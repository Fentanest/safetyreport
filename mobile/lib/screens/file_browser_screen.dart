import 'package:flutter/material.dart';
import 'package:provider/provider.dart';
import '../models/file_item.dart';
import '../providers/report_provider.dart';
import '../services/api_service.dart';

class FileBrowserScreen extends StatefulWidget {
  const FileBrowserScreen({super.key});

  @override
  State<FileBrowserScreen> createState() => _FileBrowserScreenState();
}

class _FileBrowserScreenState extends State<FileBrowserScreen> {
  List<FileItem>? _rootItems;
  bool _loading = true;
  String? _error;
  late ApiService _api;

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addPostFrameCallback((_) {
      final p = context.read<ReportProvider>();
      _api = ApiService(baseUrl: p.baseUrl, apiKey: p.apiKey);
      _load('');
    });
  }

  Future<void> _load(String path) async {
    setState(() {
      _loading = true;
      _error = null;
      _rootItems = null;
    });
    try {
      final items = await _api.getFiles(path);
      if (mounted) setState(() { _rootItems = items; _loading = false; });
    } catch (e) {
      if (mounted) setState(() { _error = e.toString(); _loading = false; });
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(
        title: const Text('파일 브라우저'),
        actions: [
          IconButton(
            icon: const Icon(Icons.refresh),
            onPressed: () => _load(''),
          ),
        ],
      ),
      body: _loading
          ? const Center(child: CircularProgressIndicator())
          : _error != null
              ? Center(
                  child: Padding(
                    padding: const EdgeInsets.all(24),
                    child: Column(
                      mainAxisSize: MainAxisSize.min,
                      children: [
                        const Icon(Icons.error_outline, size: 48, color: Colors.red),
                        const SizedBox(height: 12),
                        Text(_error!, textAlign: TextAlign.center,
                            style: const TextStyle(fontSize: 13)),
                        const SizedBox(height: 16),
                        FilledButton.icon(
                          icon: const Icon(Icons.refresh),
                          label: const Text('다시 시도'),
                          onPressed: () => _load(''),
                        ),
                      ],
                    ),
                  ),
                )
              : ListView.builder(
                  itemCount: _rootItems?.length ?? 0,
                  itemBuilder: (context, i) => _TreeNode(
                    item: _rootItems![i],
                    api: _api,
                    depth: 0,
                  ),
                ),
    );
  }
}

// ──────────────────────────────────────────────
class _TreeNode extends StatefulWidget {
  final FileItem item;
  final ApiService api;
  final int depth;

  const _TreeNode({required this.item, required this.api, required this.depth});

  @override
  State<_TreeNode> createState() => _TreeNodeState();
}

class _TreeNodeState extends State<_TreeNode> {
  bool _expanded = false;
  bool _loading = false;
  List<FileItem>? _children;

  Future<void> _toggle() async {
    if (!widget.item.isDir) {
      _showDetails();
      return;
    }
    if (_expanded) { setState(() => _expanded = false); return; }
    if (_children != null) { setState(() => _expanded = true); return; }

    setState(() => _loading = true);
    try {
      final items = await widget.api.getFiles(widget.item.path);
      if (mounted) setState(() { _children = items; _expanded = true; _loading = false; });
    } catch (e) {
      if (mounted) {
        setState(() => _loading = false);
        ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text('오류: $e')));
      }
    }
  }

  void _showDetails() {
    final item = widget.item;
    showModalBottomSheet(
      context: context,
      shape: const RoundedRectangleBorder(
          borderRadius: BorderRadius.vertical(top: Radius.circular(16))),
      builder: (_) => Padding(
        padding: const EdgeInsets.fromLTRB(20, 16, 20, 32),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Center(
              child: Container(
                  width: 36, height: 4,
                  decoration: BoxDecoration(
                      color: Colors.grey.shade300,
                      borderRadius: BorderRadius.circular(2))),
            ),
            const SizedBox(height: 16),
            Row(children: [
              Icon(_fileIcon(item.name), color: Colors.blueGrey, size: 24),
              const SizedBox(width: 10),
              Expanded(child: Text(item.name,
                  style: const TextStyle(fontSize: 16, fontWeight: FontWeight.bold))),
            ]),
            const Divider(height: 24),
            _row('경로', item.path),
            _row('크기', item.size != null ? _fmt(item.size!) : '-'),
            _row('수정일', item.modified),
          ],
        ),
      ),
    );
  }

  Widget _row(String label, String value) => Padding(
    padding: const EdgeInsets.symmetric(vertical: 4),
    child: Row(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        SizedBox(width: 56,
            child: Text(label, style: const TextStyle(color: Colors.grey, fontSize: 13))),
        Expanded(child: Text(value, style: const TextStyle(fontSize: 13))),
      ],
    ),
  );

  String _fmt(int bytes) {
    if (bytes < 1024) return '$bytes B';
    if (bytes < 1024 * 1024) return '${(bytes / 1024).toStringAsFixed(1)} KB';
    return '${(bytes / (1024 * 1024)).toStringAsFixed(1)} MB';
  }

  IconData _fileIcon(String name) {
    final ext = name.contains('.') ? name.split('.').last.toLowerCase() : '';
    switch (ext) {
      case 'db': return Icons.storage;
      case 'log': return Icons.article;
      case 'csv': return Icons.table_chart;
      case 'xlsx': case 'xls': return Icons.table_chart;
      case 'json': return Icons.data_object;
      case 'txt': return Icons.text_snippet;
      case 'ini': return Icons.settings;
      case 'png': case 'jpg': case 'jpeg': return Icons.image;
      default: return Icons.insert_drive_file;
    }
  }

  @override
  Widget build(BuildContext context) {
    final item = widget.item;
    final indent = widget.depth * 20.0;

    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        InkWell(
          onTap: _toggle,
          child: Padding(
            padding: EdgeInsets.only(left: 16 + indent, right: 12, top: 10, bottom: 10),
            child: Row(
              children: [
                // 트리 라인 표시
                if (widget.depth > 0) ...[
                  SizedBox(width: 4),
                  Icon(Icons.subdirectory_arrow_right, size: 14, color: Colors.grey.shade400),
                  const SizedBox(width: 4),
                ],
                // 아이콘
                _loading
                    ? const SizedBox(
                        width: 20, height: 20,
                        child: CircularProgressIndicator(strokeWidth: 2))
                    : Icon(
                        item.isDir
                            ? (_expanded ? Icons.folder_open : Icons.folder)
                            : _fileIcon(item.name),
                        color: item.isDir ? Colors.amber.shade700 : Colors.blueGrey,
                        size: 20),
                const SizedBox(width: 8),
                Expanded(
                  child: Text(item.name,
                      style: TextStyle(
                          fontSize: 13,
                          fontWeight: item.isDir ? FontWeight.w600 : FontWeight.normal)),
                ),
                if (!item.isDir && item.size != null)
                  Text(_fmt(item.size!),
                      style: const TextStyle(fontSize: 11, color: Colors.grey)),
                if (!item.isDir)
                  Padding(
                    padding: const EdgeInsets.only(left: 8),
                    child: Text(item.modified,
                        style: const TextStyle(fontSize: 10, color: Colors.grey)),
                  ),
                if (item.isDir)
                  Icon(_expanded ? Icons.expand_less : Icons.expand_more,
                      size: 18, color: Colors.grey),
              ],
            ),
          ),
        ),
        if (_expanded && _children != null)
          ..._children!.map((child) => _TreeNode(
              item: child, api: widget.api, depth: widget.depth + 1)),
        Divider(height: 1, indent: 16 + indent, color: Colors.grey.shade100),
      ],
    );
  }
}
