@IsTest
private class E2eHeapWatchTest {
    @IsTest
    static void returnsNonNegativePeak() {
        System.assert(E2eHeapWatch.sampleHeap(3) >= 0, 'peak heap should be non-negative');
    }
}
