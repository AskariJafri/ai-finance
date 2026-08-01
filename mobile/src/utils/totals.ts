import { Transaction } from "../api/types";

// MIRRORS backend/app/repositories/transactions.py::sum_spending / sum_income.
// Change both together — this is what lets the client compute a month total
// that matches the server's dashboard/report figures exactly, instead of a
// bare sign test that double-counts credit-card payments.

export function isSpending(t: Transaction): boolean {
  // No liability-account guard here on purpose: a credit-card purchase is a
  // positive amount on a liability account, and excluding liability rows
  // would delete most of a user's spend. The card *payment* is excluded by
  // is_transfer instead.
  return !t.is_transfer && t.amount > 0;
}

export function isIncome(t: Transaction): boolean {
  return (
    !t.is_transfer &&
    t.amount < 0 &&
    t.account_type !== "credit" &&
    t.account_type !== "loan"
  );
}
