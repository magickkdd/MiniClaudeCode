"""汉诺塔 (Tower of Hanoi) 单元测试"""
from demos.hanoi import init_peg, show, move, hanoi, peg_a, peg_b, peg_c


class TestHanoi:
    def test_init(self):
        init_peg(3)
        assert peg_a == [3, 2, 1]
        assert peg_b == []
        assert peg_c == []

    def test_move_basic(self):
        src = [3, 2, 1]
        dst: list[int] = []
        move(src, dst)
        assert src == [3, 2]
        assert dst == [1]

    def test_n1_solution(self):
        init_peg(1)
        hanoi(1, peg_a, peg_b, peg_c)
        assert peg_c == [1]
        assert peg_a == []
        assert peg_b == []

    def test_n2_solution(self):
        init_peg(2)
        hanoi(2, peg_a, peg_b, peg_c)
        assert peg_c == [2, 1]
        assert peg_a == []
        assert peg_b == []

    def test_n3_solution(self):
        init_peg(3)
        hanoi(3, peg_a, peg_b, peg_c)
        assert peg_c == [3, 2, 1]
        assert peg_a == []
        assert peg_b == []

    def test_n3_step_count(self):
        """移动次数应等于 2^n - 1"""
        count = 0
        # monkey-patch move 只计数
        import demos.hanoi as mod
        orig_move = mod.move
        def counting_move(src, dst):
            nonlocal count
            count += 1
        mod.move = counting_move
        try:
            init_peg(3)
            hanoi(3, peg_a, peg_b, peg_c)
            assert count == 7
        finally:
            mod.move = orig_move
