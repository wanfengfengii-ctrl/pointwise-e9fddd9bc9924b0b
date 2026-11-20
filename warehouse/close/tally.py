from dataclasses import dataclass

from warehouse.facts.rows import normalize_measurement


@dataclass(frozen=True)
class CategoryTotal:
    category: str
    count: int
    liters: int


@dataclass(frozen=True)
class LedgerTally:
    count: int
    liters: int
    categories: tuple[CategoryTotal, ...]


def tally_rows(rows) -> LedgerTally:
    by_category = {}
    total_count = 0
    total_liters = 0
    for row in rows:
        category, liters = normalize_measurement(row.category, row.liters)
        count, volume = by_category.get(category, (0, 0))
        by_category[category] = (count + 1, volume + liters)
        total_count += 1
        total_liters += liters
    categories = tuple(CategoryTotal(category, *by_category[category]) for category in sorted(by_category))
    return LedgerTally(total_count, total_liters, categories)


def _read_amounts(data):
    count = data['count']
    liters = data['liters']
    if type(count) is not int or count < 0 or type(liters) is not int:
        raise ValueError('invalid_tally_amount')
    return count, liters


def tally_from_data(data) -> LedgerTally:
    if not isinstance(data, dict) or set(data) != {'count', 'liters', 'categories'}:
        raise ValueError('invalid_tally')
    count, liters = _read_amounts(data)
    if not isinstance(data['categories'], list):
        raise ValueError('invalid_category_totals')
    categories = []
    for item in data['categories']:
        if not isinstance(item, dict) or set(item) != {'category', 'count', 'liters'}:
            raise ValueError('invalid_category_total')
        category_count, category_liters = _read_amounts(item)
        category, _ = normalize_measurement(item['category'], category_liters)
        categories.append(CategoryTotal(category, category_count, category_liters))
    return LedgerTally(count, liters, tuple(categories))
