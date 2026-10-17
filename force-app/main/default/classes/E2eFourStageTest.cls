@IsTest
private class E2eFourStageTest {
    @IsTest
    static void countsStages() {
        System.assertEquals(4, E2eFourStage.stages(), 'four stages');
    }
}
