"""Operator-supplied call goals, facts and exact responses."""
import json
import re
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class CallDetail(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    label: str = Field(min_length=1, max_length=80)
    value: str = Field(min_length=1, max_length=200)
    kind: Literal['fact', 'identifier', 'passcode'] = 'fact'
    delivery: Literal['speech', 'dtmf'] = 'speech'
    when_asked: str = Field(default='', max_length=100)

    @model_validator(mode='after')
    def valid_delivery(self):
        if self.delivery == 'dtmf' and not re.fullmatch(r'[0-9]{1,32}', self.value):
            raise ValueError('KEYPAD_DETAILS_REQUIRE_DIGITS')
        if self.kind != 'fact' and not self.when_asked:
            raise ValueError('IDENTIFIER_REQUIRES_PROMPT_DESCRIPTION')
        if re.search(r'[<>\x00-\x1f]', self.value):
            raise ValueError('INVALID_DETAIL_VALUE')
        return self


class FixedResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    when_asked: str = Field(min_length=1, max_length=120)
    text: str = Field(min_length=1, max_length=450)


class CallSetup(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    goal: str = Field(default='', max_length=800)
    situation: str = Field(default='', max_length=1200)
    tone: Literal['worried', 'calm'] = 'calm'
    opening: str = Field(default='', max_length=160)
    details: list[CallDetail] = Field(default_factory=list, max_length=16)
    fixed_responses: list[FixedResponse] = Field(default_factory=list, max_length=16)

    def public(self):
        result = self.model_dump()
        result['details'] = [{**detail, 'value': '[withheld]'} if detail['kind'] != 'fact' else detail for detail in result['details']]
        return result

    def brief(self, scenario):
        facts = [{'id': f'detail_{index}', 'label': f'{detail.label}: {detail.value}', 'target': scenario == 'lost-wallet' and bool(re.search(r'\b(card|brand|visa|mastercard|amex|barclaycard)\b', detail.label + ' ' + detail.value, re.I))}
                 for index, detail in enumerate(self.details) if detail.kind == 'fact']
        return {'mode': 'demo', 'task_kind': scenario, 'goal': self.goal, 'situation': self.situation or 'Use only the supplied details.',
                'tone': self.tone, 'items': facts,
                **({'opening': self.opening} if self.opening else {})}


def setup_templates():
    brief = json.loads((Path(__file__).resolve().parents[2] / 'scenarios/delegate/lost-wallet-worried.json').read_text())
    return {
        'lost-wallet': CallSetup(goal=brief['goal'], situation=brief['situation'], tone=brief['tone'], opening=brief['opening'],
            details=[CallDetail(label='Card', value=item['label']) for item in brief['items'] if item['target']],
            fixed_responses=[FixedResponse(when_asked='reason for calling', text=brief['opening']),
                             FixedResponse(when_asked='which card', text='It is my Barclaycard credit card.'),
                             FixedResponse(when_asked='next steps', text='Please explain what I should do next.')]).model_dump(),
        'balance': CallSetup(goal='Find out the current balance and currency for the supplied account. Report exactly what the service says.',
            situation='I am calling to check the balance. Use only the account and verification details supplied below.',
            opening='Please tell me the current balance.',
            fixed_responses=[FixedResponse(when_asked='reason for calling', text='Please tell me the current balance.'),
                             FixedResponse(when_asked='repeat the balance', text='Please repeat the balance and currency.')]).model_dump(),
    }
