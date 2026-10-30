from tally import report, text


def test_private_pad_helper_is_gone():
    assert not hasattr(report, "_pad")


def test_report_uses_shared_pad_right():
    assert report.pad_right is text.pad_right


def test_report_output_unchanged():
    rows = [("Blue Mug", 5000), ("Tea", 399), ("Spoon set", 123456)]
    assert report.format_report(rows) == (
        "Blue Mug   $50.00\n"
        "Tea        $3.99\n"
        "Spoon set  $1,234.56"
    )


def test_empty_report():
    assert report.format_report([]) == "(no rows)"
