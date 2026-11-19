"""Map retained BOS samples to the original inclusive Cine range and its clock."""
from dataclasses import dataclass


@dataclass(frozen=True)
class RangeSampling:
    source_count: int
    step: int = 1

    def __post_init__(self):
        if type(self.source_count) is not int or self.source_count < 1:
            raise ValueError('Choose at least one source frame.')
        if type(self.step) is not int or self.step < 1:
            raise ValueError('Analyze every Nth frame requires a positive whole number.')

    @property
    def count(self):
        # Mark Out is always included, even when the last interval is short.
        return 1 + (self.source_count - 1 + self.step - 1) // self.step

    def offset(self, index):
        """Original offset of a retained sample, or the exclusive end boundary."""
        if index == self.count:
            return self.source_count
        return min(index * self.step, self.source_count - 1)

    def index_at(self, offset):
        """Hold the most recent measured sample until the next sample's time."""
        if offset >= self.source_count - 1:
            return self.count - 1
        return max(0, int(offset) // self.step)

    def nearest(self, offset):
        left = self.index_at(offset)
        if left < self.count - 1 and offset - self.offset(left) > self.offset(left+1) - offset:
            return left + 1
        return left
