import { describe, expect, it } from 'vitest'
import { parseSQLStatements } from '../sqlStatements'

describe('parseSQLStatements', () => {
  it('splits on semicolons and drops empty statements', () => {
    expect(parseSQLStatements('SELECT 1;; ;SELECT 2;')).toEqual(['SELECT 1', 'SELECT 2'])
    expect(parseSQLStatements('   ')).toEqual([])
  })

  it('keeps semicolons inside string literals', () => {
    expect(parseSQLStatements("SELECT 'a;b' FROM t;")).toEqual(["SELECT 'a;b' FROM t"])
    expect(parseSQLStatements('SELECT "x;y" FROM t;')).toEqual(['SELECT "x;y" FROM t'])
    expect(parseSQLStatements('SELECT `a;b` FROM t;')).toEqual(['SELECT `a;b` FROM t'])
  })

  it('supports SQL-style escaped quote and backslash escape inside strings', () => {
    expect(parseSQLStatements("SELECT 'it''s;a' FROM t;")).toEqual(["SELECT 'it''s;a' FROM t"])
    expect(parseSQLStatements("SELECT 'a\\';b' FROM t;")).toEqual(["SELECT 'a\\';b' FROM t"])
  })

  it('ignores semicolons inside line comments of both styles', () => {
    expect(parseSQLStatements('SELECT 1 -- c1;c2\n;SELECT 2')).toEqual(['SELECT 1 -- c1;c2', 'SELECT 2'])
    expect(parseSQLStatements('SELECT 1 # hash;comment\n;SELECT 2')).toEqual(['SELECT 1 # hash;comment', 'SELECT 2'])
  })

  it('ignores semicolons inside block comments', () => {
    expect(parseSQLStatements('SELECT /* a;b */ 1;')).toEqual(['SELECT /* a;b */ 1'])
  })

  it('returns trailing statement without semicolon', () => {
    expect(parseSQLStatements('SELECT 1;SELECT 2')).toEqual(['SELECT 1', 'SELECT 2'])
  })
})
