import json
from tests.test_slide_layout_qa import _seq
from app.services.slide_sequence import expand_primitives, _tight_rect
topic = {"template": "list", "scene": 2, "anchor": "삼성전자와", "title": "AI 반도체 관련주", "items": ["삼성전자", "SK하이닉스"]}
seq = _seq([topic])
for p in expand_primitives(seq):
    print(p['kind'], p.get('text'), p['pos'], p.get('align'), [round(v) for v in _tight_rect(p)])
