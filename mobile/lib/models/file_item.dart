class FileItem {
  final String name;
  final String path;
  final bool isDir;
  final int? size;
  final String modified;

  const FileItem({
    required this.name,
    required this.path,
    required this.isDir,
    this.size,
    required this.modified,
  });

  factory FileItem.fromJson(Map<String, dynamic> json) {
    return FileItem(
      name: json['name']?.toString() ?? '',
      path: json['path']?.toString() ?? '',
      isDir: json['is_dir'] == true,
      size: (json['size'] as num?)?.toInt(),
      modified: json['modified']?.toString() ?? '',
    );
  }
}
