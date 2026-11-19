import unittest
from workspace_job import validate_pane_view


class PaneJobValidationTests(unittest.TestCase):
    def test_older_jobs_and_all_layouts_are_valid(self):
        for count in range(5):
            validate_pane_view({},count)
            for layout in ('auto','grid','side_by_side','stacked'):
                validate_pane_view(dict(pane_order=list(reversed(range(count))),pane_layout=layout,play_visible=True),count)

    def test_order_preserves_source_identity_and_rejects_duplicate_or_invalid_slots(self):
        for order in ([0,0],[0],[0,2],[-1,0],[False,1],[0.,1.],None,'01'):
            with self.subTest(order=order),self.assertRaises(ValueError):validate_pane_view({'pane_order':order},2)
        for value in ('tiles',None,3):
            with self.assertRaises(ValueError):validate_pane_view({'pane_layout':value},2)
        for value in ('yes',1,None):
            with self.assertRaises(ValueError):validate_pane_view({'play_visible':value},2)


if __name__=='__main__':unittest.main()
