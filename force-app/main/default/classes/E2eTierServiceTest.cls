@IsTest
private class E2eTierServiceTest {
    @IsTest
    static void tiersByRevenue() {
        Assert.areEqual('Unknown', E2eTierService.tierFor(null));
        Assert.areEqual('Platinum', E2eTierService.tierFor(2500000));
        Assert.areEqual('Gold', E2eTierService.tierFor(250000));
        Assert.areEqual('Silver', E2eTierService.tierFor(500));
    }

    @IsTest
    static void discountsByTier() {
        Assert.areEqual(20, E2eTierService.discountPercent('Platinum'));
        Assert.areEqual(10, E2eTierService.discountPercent('Gold'));
        Assert.areEqual(0, E2eTierService.discountPercent('Silver'));
    }
}
