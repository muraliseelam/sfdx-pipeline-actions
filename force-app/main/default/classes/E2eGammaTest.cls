@IsTest
private class E2eGammaTest {
    @IsTest
    static void returnsName() {
        System.assertEquals('Gamma', E2eGamma.name(), 'name');
    }
}
