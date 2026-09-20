"""
汉诺塔 (Tower of Hanoi) —— 递归可视化演示
用法: python demos/hanoi.py [n]
  n 为圆盘数，默认 4，建议 1~8
"""
import sys, time

# 三根柱子用列表模拟，末尾是栈顶
peg_a: list[int] = []
peg_b: list[int] = []
peg_c: list[int] = []


def init_peg(n: int) -> None:
    peg_a.clear()
    peg_b.clear()
    peg_c.clear()
    peg_a.extend(range(n, 0, -1))  # 大圈在下、小圈在上


def show() -> None:
    max_w = max(len(peg_a), len(peg_b), len(peg_c), 1)
    print("─" * 40)
    print(f" A: {peg_a}")
    print(f" B: {peg_b}")
    print(f" C: {peg_c}")
    print("─" * 40)


def move(src: list[int], dst: list[int]) -> None:
    disk = src.pop()
    dst.append(disk)
    show()
    time.sleep(0.3)


def hanoi(n: int, src: list[int], mid: list[int], dst: list[int]) -> None:
    if n == 0:
        return
    hanoi(n - 1, src, dst, mid)
    move(src, dst)
    hanoi(n - 1, mid, src, dst)


def main() -> None:
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 4
    print(f"\n汉诺塔，n = {n} 个圆盘，初始全在 A\n")
    init_peg(n)
    show()
    hanoi(n, peg_a, peg_b, peg_c)
    print(f"\n完成！共 {n} 层圆盘全部移到 C。")


if __name__ == "__main__":
    main()
