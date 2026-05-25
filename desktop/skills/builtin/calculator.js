'use strict';

const { BaseSkill } = require('../base-skill');

/**
 * Whitelisted Math functions and constants available in the sandbox.
 * Only safe, deterministic math operations are allowed.
 */
const MATH_WHITELIST = {
  abs: Math.abs,
  ceil: Math.ceil,
  floor: Math.floor,
  round: Math.round,
  max: Math.max,
  min: Math.min,
  pow: Math.pow,
  sqrt: Math.sqrt,
  cbrt: Math.cbrt,
  log: Math.log,
  log2: Math.log2,
  log10: Math.log10,
  exp: Math.exp,
  sign: Math.sign,
  trunc: Math.trunc,
  sin: Math.sin,
  cos: Math.cos,
  tan: Math.tan,
  asin: Math.asin,
  acos: Math.acos,
  atan: Math.atan,
  atan2: Math.atan2,
  sinh: Math.sinh,
  cosh: Math.cosh,
  tanh: Math.tanh,
  hypot: Math.hypot,
  random: Math.random,
  PI: Math.PI,
  E: Math.E,
  LN2: Math.LN2,
  LN10: Math.LN10,
  SQRT2: Math.SQRT2,
  SQRT1_2: Math.SQRT1_2,
  Infinity: Infinity,
  NaN: NaN,
};

/**
 * CalculatorSkill — evaluates mathematical expressions safely.
 * Uses a sandboxed Function with only Math.* available.
 */
class CalculatorSkill extends BaseSkill {
  constructor() {
    super({
      name: 'calculator',
      description: 'Evaluate a mathematical expression safely. Supports standard math operations and Math.* functions (sin, cos, sqrt, pow, log, etc.).',
      version: '1.0.0',
      category: 'utility',
      parameters: {
        type: 'object',
        properties: {
          expression: {
            type: 'string',
            description: 'Mathematical expression to evaluate, e.g. "sqrt(144) + pow(2, 10)"',
          },
        },
        required: ['expression'],
      },
    });
  }

  /**
   * Validate that the expression contains only safe characters.
   * @param {string} expr
   * @throws {Error} If expression contains unsafe patterns.
   * @private
   */
  _validate(expr) {
    // Block obvious code injection: no assignment, no function keywords,
    // no require/import/eval/process/global, no backticks, no semicolons
    const forbidden = /(\bfunction\b|\beval\b|\brequire\b|\bimport\b|\bprocess\b|\bglobal\b|\bthis\b|\bnew\b|\breturn\b[^;]*\breturn\b|`|;|\bwhile\b|\bfor\b|\bclass\b|\bconstructor\b|\b__proto__\b|\bprototype\b)/i;
    if (forbidden.test(expr)) {
      throw new Error('Expression contains forbidden keywords');
    }

    // Only allow: digits, operators, parens, commas, dots, spaces, and alpha (for function names)
    const allowed = /^[0-9a-zA-Z_\s+\-*/().,%^!<>=&|?:]+$/;
    if (!allowed.test(expr)) {
      throw new Error('Expression contains invalid characters');
    }
  }

  /**
   * Execute the calculation.
   * @param {{expression: string}} args
   * @returns {Promise<{expression: string, result: number}>}
   */
  async execute(args) {
    if (!args || !args.expression || typeof args.expression !== 'string') {
      throw new Error('Parameter "expression" is required and must be a string');
    }

    const expr = args.expression.trim();
    if (!expr) {
      throw new Error('Expression cannot be empty');
    }
    if (expr.length > 500) {
      throw new Error('Expression too long (max 500 characters)');
    }

    this._validate(expr);

    // Build sandboxed context: only Math functions are available
    const paramNames = Object.keys(MATH_WHITELIST);
    const paramValues = Object.values(MATH_WHITELIST);

    try {
      // Create a function with math context as parameters
      // eslint-disable-next-line no-new-func
      const fn = new Function(...paramNames, `"use strict"; return (${expr});`);
      const result = fn(...paramValues);

      if (typeof result !== 'number' && typeof result !== 'boolean') {
        throw new Error('Expression did not return a numeric result');
      }

      return {
        expression: expr,
        result: Number(result),
      };
    } catch (err) {
      if (err.message.includes('Expression')) {
        throw err;
      }
      throw new Error(`Calculation error: ${err.message}`);
    }
  }
}

module.exports = new CalculatorSkill();
