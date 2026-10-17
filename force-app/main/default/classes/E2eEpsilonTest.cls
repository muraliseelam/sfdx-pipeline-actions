@IsTest
private class E2eEpsilonTest {
    @IsTest
    static void returnsName() {
        System.assertEquals('Epsilon', E2eEpsilon.name(), 'name');
    }
}
