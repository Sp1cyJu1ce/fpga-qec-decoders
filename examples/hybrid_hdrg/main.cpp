#include "Vtb_hybrid_hdrg.h"
#include "verilated.h"

int main(int argc, char** argv) {
    VerilatedContext context;
    context.commandArgs(argc, argv);
    Vtb_hybrid_hdrg top(&context);
    while (!context.gotFinish()) {
        top.eval();
        if (!top.eventsPending()) break;
        context.time(top.nextTimeSlot());
    }
    top.final();
    return context.gotFinish() && !context.gotError() ? 0 : 1;
}
