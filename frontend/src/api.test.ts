import {describe,it,expect} from 'vitest';
import {eventWord,flag,money,date} from './api';
describe('Русские форматы и честные неизвестные значения',()=>{
 it.each([[0,'событий'],[1,'событие'],[2,'события'],[11,'событий'],[14,'событий'],[21,'событие'],[112,'событий']])('число %i', (n,w)=>expect(eventWord(n as number)).toBe(w));
 it('не смешивает неизвестное и ложный признак',()=>{expect(flag(null)).toBe('Нет данных');expect(flag(false)).toBe('Не отмечен')});
 it('форматирует точную выручку и московскую дату',()=>{expect(money('62340483339')).toContain('62,34');expect(date('2026-10-22T22:00:00Z')).toContain('23.10.2026')});
});
