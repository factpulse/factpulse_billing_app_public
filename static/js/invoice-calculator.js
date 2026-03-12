/**
 * invoice-calculator.js — Pure calculation functions for invoice totals.
 *
 * Used by Alpine.js for live preview AND to populate en16931_data payload.
 * FactPulse remains the final judge — these are optimistic calculations.
 *
 * Key names match the FactPulse API format.
 */

const InvoiceCalculator = {
    /**
     * Calculate line net amount: quantity × unitNetPrice
     * Rounds to 2 decimal places.
     */
    lineNetAmount(quantity, unitNetPrice) {
        const qty = parseFloat(quantity) || 0;
        const price = parseFloat(unitNetPrice) || 0;
        return (qty * price).toFixed(2);
    },

    /**
     * Calculate totals and VAT lines from invoice lines.
     *
     * @param {Array} lines - Array of line objects with quantity, unitNetPrice, manualVatRate, vatCategory
     * @returns {Object} { totals, vatLines }
     */
    calculateTotals(lines) {
        const vatMap = {};
        let totalNetAmount = 0;

        for (const line of lines) {
            const netAmount = parseFloat(this.lineNetAmount(line.quantity, line.unitNetPrice));
            totalNetAmount += netAmount;

            const rate = line.manualVatRate || '20.00';
            const category = line.vatCategory || 'S';
            const key = `${category}_${rate}`;

            if (!vatMap[key]) {
                vatMap[key] = {
                    category: category,
                    manualRate: rate,
                    taxableAmount: 0,
                    exemptionReason: line.exemptionReason || '',
                };
            }
            vatMap[key].taxableAmount += netAmount;
        }

        let vatAmountTotal = 0;
        const vatLines = Object.values(vatMap).map(vat => {
            const vatAmount = vat.taxableAmount * (parseFloat(vat.manualRate) / 100);
            vatAmountTotal += vatAmount;
            const vatLine = {
                category: vat.category,
                manualRate: vat.manualRate,
                taxableAmount: vat.taxableAmount.toFixed(2),
                vatAmount: vatAmount.toFixed(2),
            };
            if (vat.exemptionReason) {
                vatLine.exemptionReason = vat.exemptionReason;
            }
            return vatLine;
        });

        const totalGrossAmount = totalNetAmount + vatAmountTotal;

        return {
            totals: {
                totalNetAmount: totalNetAmount.toFixed(2),
                vatAmount: vatAmountTotal.toFixed(2),
                totalGrossAmount: totalGrossAmount.toFixed(2),
            },
            vatLines: vatLines,
        };
    },
};

// Export for Node.js testing (Vitest)
if (typeof module !== 'undefined' && module.exports) {
    module.exports = InvoiceCalculator;
}
