"""独立建库脚本：运行 python -m scripts.build_index 执行一次建库。"""

from backend.rag import build_index


def main() -> None:
    stats = build_index()
    print(f"建库完成：新增 {stats['added']} 块 / {stats['chunks']} 条向量")
    print("分类分布：")
    for cat, count in stats["categories"].items():
        print(f"  - {cat}: {count}")


if __name__ == "__main__":
    main()
