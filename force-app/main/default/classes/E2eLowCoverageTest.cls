@IsTest
private class E2eLowCoverageTest {
    @IsTest
    static void coversFourOfFive() {
        Assert.areEqual(10, E2eLowCoverage.a() + E2eLowCoverage.b() + E2eLowCoverage.c() + E2eLowCoverage.d());
    }
}
