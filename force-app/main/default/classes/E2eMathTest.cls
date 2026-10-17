@IsTest
private class E2eMathTest {
    @IsTest
    static void addsWrong() {
        Assert.areEqual(5, E2eMath.add(2, 2), '2 + 2 should be 5 (intentional E2E failure)');
    }
}
