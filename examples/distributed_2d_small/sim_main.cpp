#include "Vtb.h"
#include "verilated.h"
#include <memory>

// Legacy link hook. The event loop sets time through VerilatedContext below.
double sc_time_stamp() { return 0; }

int main(int argc, char** argv) {
    auto context = std::make_unique<VerilatedContext>();
    context->commandArgs(argc, argv);
    auto model = std::make_unique<Vtb>(context.get());
    // The testbench owns the clocks. Advance to each scheduled event until
    // its checks finish, and treat a missing finish as an incomplete run.
    while (!context->gotFinish()) {
        model->eval();
        if (!model->eventsPending()) break;
        context->time(model->nextTimeSlot());
    }
    model->final();
    return context->gotFinish() ? 0 : 2;
}
