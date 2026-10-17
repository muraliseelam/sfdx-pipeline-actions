/**
 * Sample service used by the pipeline's end-to-end tests.
 */
public with sharing class E2eTierService {
    public static String tierFor(Decimal annualRevenue) {
        if (annualRevenue == null) {
            return 'Unknown';
        }
        if (annualRevenue >= 1000000) {
            return 'Platinum';
        }
        if (annualRevenue >= 100000) {
            return 'Gold';
        }
        return 'Silver';
    }

    public static Integer discountPercent(String tier) {
        switch on tier {
            when 'Platinum' {
                return 20;
            }
            when 'Gold' {
                return 10;
            }
            when else {
                return 0;
            }
        }
    }
}
