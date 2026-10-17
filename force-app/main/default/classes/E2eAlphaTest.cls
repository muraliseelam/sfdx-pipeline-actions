@IsTest
private class E2eAlphaTest {
    @IsTest
    static void returnsName() {
        System.assertEquals('Alpha', E2eAlpha.name(), 'name');
    }
}
