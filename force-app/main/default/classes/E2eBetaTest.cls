@IsTest
private class E2eBetaTest {
    @IsTest
    static void returnsName() {
        System.assertEquals('Beta', E2eBeta.name(), 'name');
    }
}
