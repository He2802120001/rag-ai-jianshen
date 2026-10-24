"""批量入库脚本：把 docs/uploads/ 下所有 .md/.txt 增量写入 ChromaDB。

用法（项目根目录）：
    .\\venv\\Scripts\\python.exe scripts\\batch_import.py

幂等：add_document 对同名文档是替换语义（先删旧块再写入），
重复运行不会产生重复向量；BM25 缓存在每次入库后自动失效待重建。
注意：本脚本在独立进程中运行，线上服务的内存 BM25 缓存不会同步刷新，
批量导入后建议重启服务（或依赖检索时的 collection count 校验，见审计 F-06）。
"""

import sys
from pathlib import Path

# 直接以脚本方式运行时，把项目根加入 sys.path 以便导入 backend 包
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.config import DOCS_DIR, UPLOADS_DIR_NAME  # noqa: E402
from backend.rag import add_document  # noqa: E402


def main() -> None:
    uploads = DOCS_DIR / UPLOADS_DIR_NAME
    uploads.mkdir(parents=True, exist_ok=True)
    files = sorted(list(uploads.glob("*.md")) + list(uploads.glob("*.txt")))
    if not files:
        print("docs/uploads/ 下没有可入库的文件。")
        return

    total_files = 0
    total_chunks = 0
    for f in files:
        stats = add_document(f)
        if stats["added"] > 0:
            total_files += 1
            total_chunks += stats["added"]
            print(f"  + {f.name}：{stats['added']} 块")
        else:
            print(f"  - {f.name}：无有效内容，跳过")

    print(f"\n批量入库完成：{total_files} 个文件 / {total_chunks} 块")


if __name__ == "__main__":
    main()
