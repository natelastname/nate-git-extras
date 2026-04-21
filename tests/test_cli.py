import goose_template

def test_entrypoint():
    goose_template.cli.entrypoint()
    assert not False is True
