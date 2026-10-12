from urllib.parse import urlparse

from insurance_app.public_living_standard import minimum_living_reference


def test_2026_minimum_living_reference_uses_surviving_household_size():
    reference = minimum_living_reference(3)

    assert reference['year'] == 2026
    assert reference['monthly_won'] == 1_714_892
    assert reference['household_size'] == 3
    assert urlparse(reference['source_url']).hostname == 'www.mohw.go.kr'
    assert '평균 지출' in reference['scope']
    assert '민간보험 권장금액' in reference['scope']


def test_minimum_living_reference_rejects_unknown_household_size():
    assert minimum_living_reference(None) is None
    assert minimum_living_reference(0) is None
